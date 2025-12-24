from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from datetime import datetime

import discord

import config
from api.birthday_models import (
    BirthdayGuildConfig,
    BirthdayListEntry,
)

# Import Repository
from repositories.birthday_repository import BirthdayRepository
from utils import TextPaginator, truncate_text

logger = logging.getLogger(__name__)


def parse_birthday(date_str: str) -> str:
    """Parse a birthday date string into a standardized format.


    Args:
        date_str: A string representing a date in either DD-MM-YYYY or YYYY-MM-DD format

    Returns:
        A string representing the parsed date in DD-MM-YYYY format

    Raises:
        ValueError: If the date string is invalid or does not match either of the
            supported formats

    """
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
    """Safely fetch a member from a guild.

    Args:
        guild: The guild to fetch the member from.
        user_id: The ID of the member to fetch.

    Returns:
        The fetched member, or None if the member is not found or if there was an error.

    """
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
    entries: Collection["BirthdayListEntry"],
    max_field_length: int = 1024,
) -> discord.Embed:
    """Create a Discord embed representing a list of birthdays.

    Args:
        guild_name: Name of the guild for which the embed is being generated.
        entries: `BirthdayListEntry` objects, each representing a user's birthday.
        max_field_length: Maximum length of a single field in the generated embed.

    Returns:
        A Discord embed containing the list of birthdays.

    Note:
        The generated embed is trimmed to Discord's constraints on embed length
        (6000 characters) and field count (25 fields).
        If the input list is too long, the generated embed may be truncated.

    """
    title = truncate_text(f"Дни рождения на сервере {guild_name}", width=256)
    embed = discord.Embed(title=title, color=discord.Color.gold())

    if not entries:
        embed.description = "Нет добавленных дней рождений."
        embed.set_footer(text="Всего дней рождений: 0")
        return embed

    lines: list[str] = []
    for i, entry in enumerate(entries, 1):
        days_until = entry["days_until"]
        days_text = "сегодня" if days_until == 0 else f"через {days_until} д."

        safe_date = truncate_text(entry["date"], width=32)
        safe_name = truncate_text(entry["name"], width=128)

        line = f"{i}. **{safe_date}** - {safe_name} ({days_text})"
        lines.append(truncate_text(line, width=max_field_length, mode="end"))

    paginator = TextPaginator(
        lines,
        page_size=20,
        max_length=max_field_length,
        separator="\n",
    )

    MAX_FIELDS = 25
    MAX_TOTAL = 6000

    for page_num, page_text in enumerate(paginator.pages, 1):
        if len(embed.fields) >= MAX_FIELDS:
            break

        field_name = (
            "Ближайшие дни рождения"
            if page_num == 1
            else f"Ближайшие дни рождения (стр. {page_num})"
        )
        field_name = truncate_text(field_name, width=256)

        projected_total = len(embed) + len(field_name) + len(page_text)
        if projected_total > MAX_TOTAL:
            break

        embed.add_field(name=field_name, value=page_text, inline=False)

    footer = f"Всего дней рождений: {len(entries)}"
    embed.set_footer(text=truncate_text(footer, width=2048))
    return embed


class BirthdayManager:
    def __init__(self, repository: BirthdayRepository) -> None:
        self.repo = repository

    async def get_guild_config(self, guild_id: int) -> BirthdayGuildConfig | None:
        return await self.repo.get(guild_id)

    async def get_or_create_guild_config(
        self, guild_id: int, server_name: str, channel_id: int
    ) -> BirthdayGuildConfig:
        existing = await self.repo.get(guild_id)
        if existing is not None:
            return existing
        new_config = BirthdayGuildConfig(guild_id, server_name, channel_id)
        await self.repo.save(new_config)
        return new_config

    async def save_guild_config(self, guild_config: BirthdayGuildConfig) -> None:
        await self.repo.save(guild_config)

    async def delete_guild_config(self, guild_id: int) -> bool:
        existing = await self.repo.get(guild_id)
        if existing:
            await self.repo.delete(guild_id)
            return True
        return False

    async def get_all_guild_ids(self) -> list[int]:
        return await self.repo.get_all_guild_ids()


birthday_manager = BirthdayManager(BirthdayRepository())
