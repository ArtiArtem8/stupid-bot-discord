"""Inventory Discord guilds and explicitly seed reviewed language overrides offline."""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import discord
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from stupidbot import settings
from stupidbot.common.json import JsonArray, JsonObject, get_json
from stupidbot.i18n.policy import Language
from stupidbot.storage.database import Database, open_engine, validate_schema
from stupidbot.storage.identity import discord_id, ensure_guild
from stupidbot.storage.schema import guild_preferences


@dataclass(frozen=True, slots=True)
class GuildLanguage:
    """An operator-reviewed language for a guild from the complete inventory."""

    guild_id: int
    language: Language


@dataclass(frozen=True, slots=True)
class RolloutResult:
    """Counts after preview or committed seeding; existing rows are never changed."""

    inserted: int
    preserved: int


async def inventory(token: str) -> JsonObject:
    """Read every guild through REST without a gateway connection or command sync.

    A failed request aborts the inventory instead of publishing a partial list.
    Russian is the proposed language; each row must be reviewed before the
    separate database operation accepts it.
    """
    guilds: JsonArray = []
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        if client.user is None:
            raise RuntimeError("Discord login returned no bot identity")
        application_id = client.user.id
        async for partial in client.fetch_guilds(limit=None):
            guild = await client.fetch_guild(partial.id, with_counts=False)
            guilds.append(
                {
                    "guild_id": str(guild.id),
                    "name": guild.name,
                    "discord_locale": guild.preferred_locale.value,
                    "language": Language.RU.value,
                    "reviewed": False,
                }
            )
    return {
        "format": 1,
        "application_id": str(application_id),
        "captured_at": datetime.now(UTC).isoformat(),
        "guild_count": len(guilds),
        "guilds": guilds,
    }


def read_reviewed_inventory(path: Path) -> tuple[GuildLanguage, ...]:
    """Reject partial, duplicate, invalid or unreviewed manifests before SQL access."""
    payload = get_json(path)
    if payload is None or payload.get("format") != 1:
        raise ValueError("Expected a version 1 guild inventory")
    rows = payload.get("guilds")
    if not isinstance(rows, list) or payload.get("guild_count") != len(rows):
        raise ValueError("Inventory guild_count does not match its rows")
    return _reviewed_guilds(rows)


def _reviewed_guilds(rows: JsonArray) -> tuple[GuildLanguage, ...]:
    entries: list[GuildLanguage] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("reviewed") is not True:
            raise ValueError("Every guild requires an explicit reviewed=true decision")
        raw_id, language = row.get("guild_id"), row.get("language")
        if not isinstance(raw_id, str) or not raw_id.isdecimal():
            raise ValueError("guild_id must be a decimal Discord ID string")
        guild_id = discord_id(int(raw_id))
        if guild_id in seen or not isinstance(language, str):
            raise ValueError("Duplicate guild or missing language decision")
        entries.append(GuildLanguage(guild_id, Language(language)))
        seen.add(guild_id)
    return tuple(entries)


async def seed_languages(
    database: Database, entries: tuple[GuildLanguage, ...], *, apply: bool
) -> RolloutResult:
    """Seed absent guild preferences in one transaction, preserving all existing rows.

    Call with the bot stopped and a prepared database under exclusive maintenance
    ownership. Even an existing row with a null language is intentional and stays
    untouched. Preview performs no identity inserts or settings writes.
    """
    await validate_schema(database)
    inserted = preserved = 0
    async with database.transaction() as connection:
        for entry in entries:
            current = await connection.scalar(
                select(guild_preferences.c.subject_id).where(
                    guild_preferences.c.subject_id == entry.guild_id
                )
            )
            if current is not None:
                preserved += 1
                continue
            inserted += 1
            if apply:
                await ensure_guild(connection, entry.guild_id)
                await connection.execute(
                    insert(guild_preferences)
                    .values(
                        subject_id=entry.guild_id,
                        language=entry.language,
                        timezone=None,
                        revision=1,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[guild_preferences.c.subject_id]
                    )
                )
    return RolloutResult(inserted, preserved)


class Arguments(argparse.Namespace):
    """Explicit paths keep inventory and deployment away from default bot data."""

    command: str = ""
    output: Path = Path()
    manifest: Path = Path()
    database: Path = Path()
    apply: bool = False


async def _seed(args: Arguments, entries: tuple[GuildLanguage, ...]) -> RolloutResult:
    database = Database(open_engine(args.database))
    try:
        return await seed_languages(database, entries, apply=args.apply)
    finally:
        await database.close()


def main() -> None:
    """Inventory first, review every row, then preview or explicitly apply offline."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("inventory", help="Read Discord guilds; no database")
    export.add_argument("--output", type=Path, required=True)
    seed = commands.add_parser("seed", help="Preview reviewed overrides by default")
    seed.add_argument("--manifest", type=Path, required=True)
    seed.add_argument("--database", type=Path, required=True)
    seed.add_argument("--apply", action="store_true")
    args = parser.parse_args(namespace=Arguments())
    if args.command == "inventory":
        token = settings.DISCORD_BOT_TOKEN
        if not token:
            parser.error("DISCORD_BOT_TOKEN is required for the read-only inventory")
        payload = asyncio.run(inventory(token))
        # Exclusive creation prevents replacing an operator's reviewed decisions.
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        sys.stdout.write(
            f"Inventoried {payload['guild_count']} guilds; review {args.output}\n"
        )
    else:
        entries = read_reviewed_inventory(args.manifest)
        result = asyncio.run(_seed(args, entries))
        mode = "Committed" if args.apply else "Preview"
        sys.stdout.write(
            f"{mode}: {result.inserted} new overrides; "
            f"{result.preserved} existing rows preserved\n"
        )


if __name__ == "__main__":
    main()
