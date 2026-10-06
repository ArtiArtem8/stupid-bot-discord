"""Strict, read-only decoding of the finite legacy storage snapshot."""

import gzip
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from api.birthday_models import BirthdayGuildConfig
from api.blocking_models import BlockedUser, BlockHistoryEntry, NameHistoryEntry
from api.monitor_models import MonitorSettings
from api.report_models import ReportDataDict
from api.voice.model import VoiceJournalRecord
from repositories.sqlite.identity import discord_id, utc_microseconds
from repositories.volume_repository import VolumeData
from tools.storage_legacy.birthdays import load_birthdays
from tools.storage_legacy.json_input import identifier, load_object, load_volumes
from tools.storage_legacy.voice_codec import decode_record
from utils.json_types import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class LegacyRoleSnapshot:
    user_id: int
    username: str
    roles: list[int]
    left_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyMonitor:
    guild_id: int
    settings: MonitorSettings
    snapshots: list[LegacyRoleSnapshot]


@dataclass(slots=True)
class LegacyData:
    """Validated source values; no source is a live runtime backend."""

    birthdays: list[BirthdayGuildConfig] = field(
        default_factory=list[BirthdayGuildConfig]
    )
    volumes: list[VolumeData] = field(default_factory=list[VolumeData])
    blocks: list[tuple[int, BlockedUser]] = field(
        default_factory=list[tuple[int, BlockedUser]]
    )
    reports: list[ReportDataDict] = field(default_factory=list[ReportDataDict])
    report_channel: int | None = None
    questions: list[tuple[int, str, str]] = field(
        default_factory=list[tuple[int, str, str]]
    )
    monitors: list[LegacyMonitor] = field(default_factory=list[LegacyMonitor])
    uptime: tuple[int, int] | None = None
    voice: list[VoiceJournalRecord] = field(default_factory=list[VoiceJournalRecord])
    voice_resolutions: JsonObject = field(default_factory=dict[str, JsonValue])
    sources: dict[Path, tuple[int, str]] = field(
        default_factory=dict[Path, tuple[int, str]]
    )


def _obj(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _optional_string(value: JsonValue) -> str | None:
    return None if value is None else _string(value)


def _array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return value


def _boolean(value: JsonValue) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean")
    return value


def _aware(value: JsonValue) -> datetime:
    moment = datetime.fromisoformat(_string(value))
    utc_microseconds(moment)
    return moment


def _normalized_items(data: JsonObject) -> list[tuple[int, JsonValue]]:
    result: dict[int, JsonValue] = {}
    for key, value in data.items():
        normalized = identifier(key)
        if normalized in result:
            raise ValueError("Duplicate normalized identity")
        result[normalized] = value
    return list(result.items())


def _actions(value: JsonValue) -> list[BlockHistoryEntry]:
    result: list[BlockHistoryEntry] = []
    for item in _array(value):
        entry = _obj(item)
        result.append(
            BlockHistoryEntry(
                identifier(entry.get("admin_id")),
                _string(entry.get("reason")),
                _aware(entry.get("timestamp")),
            )
        )
    return result


def _blocks(path: Path) -> list[tuple[int, BlockedUser]]:
    result: list[tuple[int, BlockedUser]] = []
    for gid, raw_guild in _normalized_items(load_object(path)):
        for uid, raw in _normalized_items(_obj(_obj(raw_guild).get("users"))):
            user = _obj(raw)
            if identifier(user.get("user_id")) != uid:
                raise ValueError("Block user ID disagrees with its object key")
            names: list[NameHistoryEntry] = []
            for item in _array(user.get("name_history")):
                entry = _obj(item)
                names.append(
                    NameHistoryEntry(
                        _string(entry.get("username")), _aware(entry.get("timestamp"))
                    )
                )
            result.append(
                (
                    gid,
                    BlockedUser(
                        uid,
                        _string(user.get("current_username")),
                        _optional_string(user.get("current_global_name")),
                        _actions(user.get("block_history")),
                        _actions(user.get("unblock_history")),
                        names,
                        _boolean(user.get("blocked")),
                    ),
                )
            )
    return result


def _report(value: JsonValue) -> ReportDataDict:
    raw = _obj(value)
    user, guild, channel = (
        _obj(raw.get("user")),
        _obj(raw.get("guild")),
        _obj(raw.get("channel")),
    )
    return ReportDataDict(
        user={
            "id": discord_id(user.get("id")),
            "name": _string(user.get("name")),
            "avatar": _optional_string(user.get("avatar")),
        },
        guild={
            "id": discord_id(guild["id"]) if guild.get("id") is not None else None,
            "name": _optional_string(guild.get("name")),
        },
        channel={
            "id": discord_id(channel["id"]) if channel.get("id") is not None else None,
            "name": _optional_string(channel.get("name")),
        },
        reason=_string(raw.get("reason")),
        created_at=_string(raw.get("created_at")),
        report_id=_string(raw.get("report_id")),
    )


def _monitor(path: Path) -> LegacyMonitor:
    guild_id = identifier(path.stem.removeprefix("guild_"))
    raw = load_object(path)
    ttl = raw.get("ttl_days")
    if ttl is not None and (
        isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0
    ):
        raise ValueError("Monitoring TTL must be a positive integer or null")
    snapshots: list[LegacyRoleSnapshot] = []
    for uid, member in _normalized_items(_obj(raw.get("members"))):
        snapshot = _obj(member)
        snapshots.append(
            LegacyRoleSnapshot(
                uid,
                _string(snapshot.get("username")),
                [discord_id(role) for role in _array(snapshot.get("roles"))],
                _aware(snapshot.get("left_at")),
            )
        )
    return LegacyMonitor(
        guild_id, MonitorSettings(_boolean(raw.get("enabled")), ttl), snapshots
    )


def _seconds(value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Uptime checkpoint requires numeric seconds")
    decimal = Decimal(str(value)) * 1_000_000
    if not decimal.is_finite() or decimal < 0:
        raise ValueError("Uptime seconds must be finite and nonnegative")
    return int(decimal.to_integral_value())


def _check_voice_pair(
    path: Path, source: Path, result: LegacyData, *, allow_prefix: bool
) -> None:
    compressed = path.with_suffix(".jsonl.gz")
    plain, full = path.read_bytes(), gzip.decompress(compressed.read_bytes())
    rule = "identical"
    if plain != full:
        if not (allow_prefix and plain.endswith(b"\n") and full.startswith(plain)):
            raise ValueError(f"Conflicting voice representations: {path}")
        rule = "exact-ordered-prefix"
    # Decode both representations: a byte prefix alone does not validate framing,
    # supported schemas, or the order of the complete records.
    prefix_records = [
        decode_record(line)
        for line in plain.decode("utf-8").splitlines()
        if line.strip()
    ]
    full_records = [
        decode_record(line)
        for line in full.decode("utf-8").splitlines()
        if line.strip()
    ]
    if prefix_records != full_records[: len(prefix_records)]:
        raise ValueError(f"Conflicting decoded voice representations: {path}")
    result.sources[path] = (len(prefix_records), f"voice-alternative:{rule}")
    result.voice_resolutions[path.relative_to(source).as_posix()] = {
        "rule": rule,
        "selected": compressed.relative_to(source).as_posix(),
        "prefix_records": len(prefix_records),
        "selected_records": len(full_records),
    }


def _voice_files(source: Path, result: LegacyData, *, allow_prefix: bool) -> list[Path]:
    files: list[Path] = []
    for root in (source / "voice_probe", source / "voice_probe" / "v2"):
        scopes = [root / "session", *sorted(root.glob("guild_*"))]
        for scope in scopes:
            if scope.name != "session":
                identifier(scope.name.removeprefix("guild_"))
            for path in sorted(scope.glob("events_*.jsonl*")):
                if not (
                    path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")
                ):
                    continue
                if (
                    path.name.endswith(".jsonl")
                    and path.with_suffix(".jsonl.gz").exists()
                ):
                    _check_voice_pair(path, source, result, allow_prefix=allow_prefix)
                    continue
                files.append(path)
    return files


def read_sources(source: Path, *, allow_voice_prefix: bool = False) -> LegacyData:
    """Read only named application stores, never recursively walk backups/exports."""
    result = LegacyData()
    _read_user_stores(source, result)
    _read_operational(source, result, allow_voice_prefix=allow_voice_prefix)
    return result


def _read_user_stores(source: Path, result: LegacyData) -> None:
    path = source / "user_birthdays.json"
    if path.exists():
        result.birthdays = load_birthdays(path)
        result.sources[path] = (len(result.birthdays), "birthdays-json")
    path = source / "music_volumes.json"
    if path.exists():
        result.volumes = load_volumes(path)
        result.sources[path] = (len(result.volumes), "volumes-json")
    path = source / "blocked_users.json"
    if path.exists():
        result.blocks = _blocks(path)
        result.sources[path] = (len(result.blocks), "blocking-json")
    path = source / "user_reports.json"
    if path.exists():
        raw = load_object(path)
        result.report_channel = (
            discord_id(raw["report_channel_id"])
            if raw.get("report_channel_id") is not None
            else None
        )
        result.reports = [_report(item) for item in _array(raw.get("reports", []))]
        result.sources[path] = (len(result.reports), "reports-json")
    path = source / "user_answers.json"
    if path.exists():
        for uid, questions in _normalized_items(load_object(path)):
            for question, answer in _obj(questions).items():
                result.questions.append((uid, question, _string(answer)))
        result.sources[path] = (len(result.questions), "questions-json")


def _read_operational(
    source: Path, result: LegacyData, *, allow_voice_prefix: bool
) -> None:
    for path in sorted((source / "guild_monitor").glob("guild_*.json")):
        monitor = _monitor(path)
        if any(previous.guild_id == monitor.guild_id for previous in result.monitors):
            raise ValueError("Duplicate normalized monitoring guild")
        result.monitors.append(monitor)
        result.sources[path] = (len(monitor.snapshots), "monitor-json")
    path = source / "last_run.json"
    if path.exists():
        raw = load_object(path)
        if raw:
            result.uptime = (
                _seconds(raw.get("last_shutdown")),
                _seconds(raw.get("accumulated_uptime")),
            )
        result.sources[path] = (int(bool(raw)), "uptime-json")
    _read_voice(source, result, allow_voice_prefix=allow_voice_prefix)


def _read_voice(source: Path, result: LegacyData, *, allow_voice_prefix: bool) -> None:
    for path in _voice_files(source, result, allow_prefix=allow_voice_prefix):
        payload = (
            gzip.decompress(path.read_bytes())
            if path.suffix == ".gz"
            else path.read_bytes()
        )
        records = [
            decode_record(line)
            for line in payload.decode("utf-8").splitlines()
            if line.strip()
        ]
        expected = (
            None
            if path.parent.name == "session"
            else identifier(path.parent.name.removeprefix("guild_"))
        )
        if any(record.guild_id != expected for record in records):
            raise ValueError("Voice record scope disagrees with its source path")
        result.voice.extend(records)
        result.sources[path] = (len(records), "voice-jsonl-v1-v2")
