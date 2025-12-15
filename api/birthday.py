from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord

import config
from api.birthday_models import (
    BirthdayGuildConfig,
    BirthdayListEntry,
)

# Import Repository
from repositories.birthday_repository import SyncBirthdayRepository

logger = logging.getLogger(__name__)


def parse_birthday(date_str: str) -> str:
    for fmt in (config.DATE_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime(config.DATE_FORMAT)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use DD-MM-YYYY or YYYY-MM-DD.")


async def safe_fetch_member(
    guild: discord.Guild, user_id: int
) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member:
        return member
    for attempt in (1, 2):
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException as e:
            if e.status >= 500 and attempt == 1:
                logger.debug("Server error fetching member %s: %s", user_id, e)
                await asyncio.sleep(2)
                continue
            logger.exception("Error fetching member %s: %s", user_id, e)
            if e.status in (400, 403, 404):
                return None
            raise


def create_birthday_list_embed(
    guild_name: str,
    entries: list[BirthdayListEntry],
    max_field_length: int = 1024,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Дни рождения на сервере {guild_name}",
        color=discord.Color.gold(),
    )
    lines: list[str] = []
    for i, entry in enumerate(entries, 1):
        days_text = (
            "сегодня" if entry["days_until"] == 0 else f"через {entry['days_until']} д."
        )
        line = f"{i}. **{entry['date']}** - {entry['name']} ({days_text})"
        lines.append(line)

    current_chunk: list[str] = []
    current_length = 0
    for line in lines:
        line_length = len(line) + 1
        if current_length + line_length > max_field_length and current_chunk:
            embed.add_field(
                name="Ближайшие дни рождения",
                value="\n".join(current_chunk),
                inline=False,
            )
            current_chunk = []
            current_length = 0
        current_chunk.append(line)
        current_length += line_length

    if current_chunk:
        embed.add_field(
            name="Ближайшие дни рождения",
            value="\n".join(current_chunk),
            inline=False,
        )
    embed.set_footer(text=f"Всего дней рождений: {len(entries)}")
    return embed


class BirthdayManager:
    def __init__(self, repository: SyncBirthdayRepository) -> None:
        self.repo = repository

    def get_guild_config(self, guild_id: int) -> BirthdayGuildConfig | None:
        return self.repo.get_guild_config(guild_id)

    def get_or_create_guild_config(
        self, guild_id: int, server_name: str, channel_id: int
    ) -> BirthdayGuildConfig:
        existing = self.repo.get_guild_config(guild_id)
        if existing is not None:
            return existing
        new_config = BirthdayGuildConfig(guild_id, server_name, channel_id)
        # Note: repo.get_guild_config caches. save_guild_config saves.
        # We need to explicitly save the new config?
        # Original code did: self._cache[guild_id] = new_config.
        # It did NOT save to file immediately in get_or_create?
        # Original: self._cache[guild_id] = new_config. return new_config.
        # No save_json called.
        # So it's in-memory until save_guild_config is called.

        # We must replicate this behavior via Repo.
        # Repo.save usually writes to disk.
        # If we want in-memory only, we bypass repo save?
        # But if we rely on repo for cache, we need to put it in repo cache.
        # I added save_guild_config to repo which writes.
        # I should probably add `cache_only=True` or just save it. Saving it is safer.
        # Let's save it.
        self.repo.save_guild_config(new_config)
        return new_config

    def save_guild_config(self, guild_config: BirthdayGuildConfig) -> None:
        self.repo.save_guild_config(guild_config)

    def delete_guild_config(self, guild_id: int) -> bool:
        return self.repo.delete_guild_config(guild_id)

    def get_all_guild_ids(self) -> list[int]:
        return self.repo.get_all_guild_ids()

    def clear_cache(self) -> None:
        self.repo.clear_cache()


birthday_manager = BirthdayManager(SyncBirthdayRepository())
