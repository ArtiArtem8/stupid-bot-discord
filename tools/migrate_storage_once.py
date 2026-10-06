"""Build and verify a new SQLite-only database from an offline legacy snapshot.

Usage: uv run --locked python -m tools.migrate_storage_once --source DATA_COPY
       --destination NEW.sqlite
The destination must not exist. Original inputs are never written or deleted.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import insert

from api.voice.model import VoiceObservation, VoiceSnapshot
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.identity import discord_id
from repositories.sqlite.schema import migration_sources, storage_state
from repositories.voice_store import VoiceStore
from tools.storage_legacy.import_features import (
    birth_date,
    import_features,
    report_time,
)
from tools.storage_legacy.json_input import load_object
from tools.storage_legacy.partial_database import read_partial
from tools.storage_legacy.sources import LegacyData, read_sources
from tools.storage_legacy.verify import verify_import
from utils.asyncio_utils import run_in_thread
from utils.json_types import JsonObject


class Arguments(argparse.Namespace):
    source: Path = Path()
    destination: Path = Path()
    source_database: Path | None = None
    source_policy: Path | None = None
    legacy_timezone: str | None = None
    allow_voice_prefix: bool = False


def _hashes(source: Path, partial: Path | None, policy: Path | None) -> dict[Path, str]:
    paths = [
        source / name
        for name in (
            "user_birthdays.json",
            "music_volumes.json",
            "blocked_users.json",
            "user_reports.json",
            "user_answers.json",
            "last_run.json",
        )
    ]
    paths.extend((source / "guild_monitor").glob("guild_*.json"))
    for root in (source / "voice_probe", source / "voice_probe" / "v2"):
        for scope in [root / "session", *root.glob("guild_*")]:
            paths.extend(scope.glob("events_*.jsonl"))
            paths.extend(scope.glob("events_*.jsonl.gz"))
    if partial is not None:
        if Path(str(partial) + "-wal").exists():
            raise ValueError(
                "Use a closed, checkpointed backup of the partial source database"
            )
        paths.append(partial)
    if policy is not None:
        paths.append(policy)
    return {
        path.resolve(): sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _choose_partial(
    data: LegacyData, database: Path | None, policy_path: Path | None
) -> None:
    if database is None:
        if policy_path is not None:
            raise ValueError("A source policy requires --source-database")
        return
    if policy_path is None:
        raise ValueError("Choose birthdays and volume explicitly with --source-policy")
    policy = load_object(policy_path)
    if set(policy) != {"birthdays", "volume"} or any(
        value not in ("json", "sqlite") for value in policy.values()
    ):
        raise ValueError(
            'Source policy must name birthdays and volume as "json" or "sqlite"'
        )
    birthdays, volumes, revision = read_partial(database)
    if policy["birthdays"] == "sqlite":
        data.birthdays = birthdays
    if policy["volume"] == "sqlite":
        data.volumes = volumes
    data.sources[database] = (len(birthdays) + len(volumes), revision)
    data.sources[policy_path] = (2, "explicit-source-policy")


def _validate(data: LegacyData, timezone: ZoneInfo | None) -> None:
    for guild in data.birthdays:
        discord_id(guild.guild_id)
        for user in guild.users.values():
            discord_id(user.user_id)
            birth_date(user.birthday)
    for volume in data.volumes:
        discord_id(volume.guild_id)
        if not 0 <= volume.volume <= 200:
            raise ValueError("Resolve invalid volume before importing")
    _validate_scopes(data)
    identifiers: set[str] = set()
    for report in data.reports:
        report_time(report["created_at"], timezone)
        if not report["report_id"] or report["report_id"] in identifiers:
            raise ValueError("Duplicate or empty report ID")
        identifiers.add(report["report_id"])


def _validate_scopes(data: LegacyData) -> None:
    """Reject contradictory Discord scopes before creating the staging database."""
    channels: dict[int, int] = {}
    roles: dict[int, int] = {}

    for guild in data.birthdays:
        _observe_scope(channels, guild.channel_id, guild.guild_id)
        _observe_scope(roles, guild.birthday_role_id, guild.guild_id)
    for monitor in data.monitors:
        for snapshot in monitor.snapshots:
            for role in snapshot.roles:
                _observe_scope(roles, role, monitor.guild_id)
    for report in data.reports:
        guild_id = report["guild"]["id"]
        if guild_id is not None:
            _observe_scope(channels, report["channel"]["id"], guild_id)
    _validate_voice_scopes(data, channels)


def _observe_scope(mapping: dict[int, int], identity: int | None, guild: int) -> None:
    discord_id(guild)
    if identity is None:
        return
    discord_id(identity)
    if mapping.setdefault(identity, guild) != guild:
        raise ValueError(f"Conflicting guild scope for Discord ID {identity}")


def _validate_voice_scopes(data: LegacyData, channels: dict[int, int]) -> None:
    for record in data.voice:
        if record.guild_id is None:
            continue
        if isinstance(record.fact, VoiceObservation):
            _observe_scope(channels, record.fact.state.channel_id, record.guild_id)
        elif isinstance(record.fact, VoiceSnapshot):
            for state in record.fact.states:
                _observe_scope(channels, state.channel_id, record.guild_id)


def _manifest(
    data: LegacyData, hashes: dict[Path, str], source: Path, timezone: ZoneInfo | None
) -> tuple[JsonObject, str]:
    files: JsonObject = {}
    descriptions = {
        path.resolve(): description for path, description in data.sources.items()
    }
    for path, digest in sorted(hashes.items()):
        count, format_name = descriptions.get(
            path, (0, "matching-voice-representation")
        )
        name = (
            path.relative_to(source).as_posix()
            if path.is_relative_to(source)
            else str(path)
        )
        files[name] = {"sha256": digest, "count": count, "format": format_name}
    bounds = [record.observed_at.astimezone(UTC).isoformat() for record in data.voice]
    manifest: JsonObject = {
        "sources": files,
        "voice_resolutions": data.voice_resolutions,
        "voice_observed_min": min(bounds) if bounds else None,
        "voice_observed_max": max(bounds) if bounds else None,
        "legacy_timezone": str(timezone) if timezone else None,
    }
    digest = sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest, digest


async def migrate_snapshot(args: Arguments) -> JsonObject:
    """Publish after verification; refuse existing destinations without writing."""
    source, destination = args.source.resolve(), args.destination.resolve()
    if not source.is_dir():
        raise ValueError("Source must be an explicit offline data directory")
    building = destination.with_name(destination.name + ".building.sqlite")
    if destination.exists() or building.exists():
        raise FileExistsError(
            "Destination or its unfinished building file already exists"
        )
    timezone = ZoneInfo(args.legacy_timezone) if args.legacy_timezone else None
    original = await run_in_thread(
        lambda: _hashes(source, args.source_database, args.source_policy)
    )
    data = await run_in_thread(
        lambda: read_sources(source, allow_voice_prefix=args.allow_voice_prefix)
    )
    await run_in_thread(
        lambda: _choose_partial(data, args.source_database, args.source_policy)
    )
    _validate(data, timezone)
    manifest, digest = _manifest(data, original, source, timezone)
    await _unchanged(args, original)
    # Reserve a new path without truncating a racing writer's destination.
    await run_in_thread(lambda: _reserve_and_migrate(building))
    database = Database(open_engine(building))
    try:
        async with database.transaction() as connection:
            await connection.execute(
                storage_state.update()
                .where(storage_state.c.singleton == 1)
                .values(status="BUILDING", manifest_hash=digest, origin="legacy_import")
            )
        await import_features(database, data, timezone)
        voice = VoiceStore(database)
        for offset in range(0, len(data.voice), 500):
            await voice.append(
                f"import:{digest}:{offset}", data.voice[offset : offset + 500]
            )
        await verify_import(database, data)
        await _unchanged(args, original)
        async with database.transaction() as connection:
            descriptions = {path.resolve(): item for path, item in data.sources.items()}
            for path in original:
                description = descriptions.get(
                    path, (0, "matching-voice-representation")
                )
                resolved = path.resolve()
                name = (
                    resolved.relative_to(source).as_posix()
                    if resolved.is_relative_to(source)
                    else str(resolved)
                )
                await connection.execute(
                    insert(migration_sources).values(
                        source_name=name,
                        digest=original[resolved],
                        record_count=description[0],
                        schema_version=description[1],
                        bounds_hint=json.dumps(
                            {
                                "voice_observed_min": manifest["voice_observed_min"],
                                "voice_observed_max": manifest["voice_observed_max"],
                                "scope": "entire imported voice history",
                                "voice_resolution": data.voice_resolutions.get(name),
                                "legacy_timezone": args.legacy_timezone,
                            },
                            sort_keys=True,
                        ),
                    )
                )
            await connection.execute(
                storage_state.update()
                .where(storage_state.c.singleton == 1)
                .values(status="COMPLETE")
            )
    finally:
        await database.close()
    await run_in_thread(lambda: _publish(building, destination))
    return {
        "database": str(destination),
        "manifest_sha256": digest,
        "manifest": manifest,
        "verified": True,
        "uptime_history": "one imported checkpoint period; earlier periods unknown",
    }


async def _unchanged(args: Arguments, original: dict[Path, str]) -> None:
    current = await run_in_thread(
        lambda: _hashes(args.source.resolve(), args.source_database, args.source_policy)
    )
    if original != current:
        raise ValueError(
            "Source snapshot changed during migration; destination remains unpublished"
        )


def _reserve_and_migrate(path: Path) -> None:
    with path.open("xb"):
        pass
    migrate(path)


def _publish(building: Path, destination: Path) -> None:
    with closing(sqlite3.connect(building, autocommit=True)) as connection:
        if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall() != [
            (0, 0, 0)
        ]:
            raise RuntimeError("Cannot publish while WAL checkpoint is busy")
    # Windows rename refuses an existing destination. All SQLite handles are closed.
    building.rename(destination)


def main() -> None:
    """Require explicit offline input and a new output; never start Discord."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-database", type=Path)
    parser.add_argument("--source-policy", type=Path)
    parser.add_argument("--legacy-timezone")
    parser.add_argument(
        "--allow-voice-prefix",
        action="store_true",
        help="Accept exact complete-record JSONL prefixes of gzip; reject divergence",
    )
    args = parser.parse_args(namespace=Arguments())
    sys.stdout.write(
        json.dumps(asyncio.run(migrate_snapshot(args)), ensure_ascii=False, indent=2)
        + "\n"
    )


if __name__ == "__main__":
    main()
