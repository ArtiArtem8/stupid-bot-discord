"""Birthday data management with typed dicts and dataclasses."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import NotRequired, Self, TypedDict

import discord

import config
from resources import MONTH_NAMES_RU
from utils.json_utils import get_json, save_json


class BirthdayListEntry(TypedDict):
    """TypedDict for birthday list display entries."""

    days_until: int
    date: str
    name: str
    user_id: int


class BirthdayUserDict(TypedDict):
    """TypedDict for raw birthday user JSON structure."""

    name: str
    birthday: str
    was_congrats: list[str]


class BirthdayGuildDict(TypedDict):
    """TypedDict for raw guild birthday JSON structure."""

    Server_name: str
    Channel_id: str
    Users: dict[str, BirthdayUserDict]
    Birthday_role: NotRequired[str | None]


def calculate_days_until_birthday(
    birthday_str: str, reference_date: date
) -> int | None:
    """Calculate days until next birthday occurrence.

    Args:
        birthday_str: Birthday in DD-MM-YYYY format
        reference_date: Date to calculate from (typically today)

    Returns:
        Number of days until birthday, or None if invalid format

    """
    if not birthday_str or len(birthday_str) != 10:
        return None

    try:
        day, month, _ = birthday_str.split("-")
        day_int = int(day)
        month_int = int(month)

        this_year_birthday = date(reference_date.year, month_int, day_int)
        if this_year_birthday >= reference_date:
            return (this_year_birthday - reference_date).days

        next_year_birthday = date(reference_date.year + 1, month_int, day_int)
        return (next_year_birthday - reference_date).days
    except ValueError:
        return None


def parse_birthday(date_str: str) -> str:
    """Parse birthday string in multiple formats.

    Accepts DD-MM-YYYY or YYYY-MM-DD and returns DD-MM-YYYY format.

    Args:
        date_str: Date string to parse

    Returns:
        Date in DD-MM-YYYY format

    Raises:
        ValidationError: If date format is invalid

    Examples:
        >>> parse_birthday("15-05-2000")
        '15-05-2000'
        >>> parse_birthday("2000-05-15")
        '15-05-2000'

    """
    for fmt in (config.DATE_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime(config.DATE_FORMAT)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use DD-MM-YYYY or YYYY-MM-DD.")


def format_birthday_date(birthday_str: str) -> str | None:
    """Format birthday string to human-readable Russian format.

    Args:
        birthday_str: Birthday in DD-MM-YYYY format

    Returns:
        Formatted string like "15 мая" or None if invalid

    """
    if not birthday_str or len(birthday_str) != 10:
        return None

    try:
        day, month, _ = birthday_str.split("-")
        day_int = int(day)
        month_int = int(month)

        if month_int not in MONTH_NAMES_RU:
            return None

        return f"{day_int} {MONTH_NAMES_RU[month_int]}"
    except ValueError:
        return None


async def safe_fetch_member(
    guild: discord.Guild, user_id: int, logger: logging.Logger
) -> discord.Member | None:
    """Safely fetch guild member with retry logic.

    Args:
        guild: Guild to fetch from
        user_id: ID of user to fetch
        logger: Logger for error reporting

    Returns:
        Member if found, None otherwise

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


@dataclass
class BirthdayUser:
    """Dataclass for internal birthday user representation."""

    user_id: int
    name: str
    birthday: str
    was_congrats: list[str] = field(default_factory=list[str])

    def has_birthday(self) -> bool:
        return bool(self.birthday and len(self.birthday) == 10)

    def birth_date(self) -> date | None:
        if not self.has_birthday():
            return None
        try:
            return datetime.strptime(self.birthday, config.DATE_FORMAT).date()
        except ValueError:
            return None

    def birth_day_month(self) -> str:
        return self.birthday[:5] if self.has_birthday() else ""

    def was_congratulated_today(self, today: date) -> bool:
        today_str = today.strftime(config.DATE_FORMAT)
        return today_str in self.was_congrats

    def add_congratulation(self, congratulation_date: date) -> None:
        date_str = congratulation_date.strftime(config.DATE_FORMAT)
        if date_str not in self.was_congrats:
            self.was_congrats.append(date_str)

    def clear_birthday(self) -> None:
        self.birthday = ""

    def to_dict(self) -> BirthdayUserDict:
        return {
            "name": self.name,
            "birthday": self.birthday,
            "was_congrats": self.was_congrats.copy(),
        }

    @classmethod
    def from_dict(cls, user_id: int, data: BirthdayUserDict) -> Self:
        return cls(
            user_id=user_id,
            name=data["name"],
            birthday=data["birthday"],
            was_congrats=data.get("was_congrats", []),
        )


@dataclass
class BirthdayGuildConfig:
    guild_id: int
    server_name: str
    channel_id: int
    users: dict[int, BirthdayUser] = field(default_factory=dict[int, BirthdayUser])
    birthday_role_id: int | None = None

    def get_user(self, user_id: int) -> BirthdayUser | None:
        return self.users.get(user_id)

    def get_or_create_user(self, user_id: int, name: str) -> BirthdayUser:
        if user_id not in self.users:
            self.users[user_id] = BirthdayUser(user_id, name, "", [])
        else:
            self.users[user_id].name = name
        return self.users[user_id]

    def remove_user(self, user_id: int) -> bool:
        if user_id in self.users:
            del self.users[user_id]
            return True
        return False

    def get_birthdays_today(self, today: date) -> list[BirthdayUser]:
        today_key = today.strftime("%d-%m")
        return [
            user
            for user in self.users.values()
            if user.birth_day_month() == today_key
            and not user.was_congratulated_today(today)
        ]

    async def get_sorted_birthday_list(
        self,
        guild: discord.Guild,
        reference_date: date,
        logger: logging.Logger,
    ) -> list[BirthdayListEntry]:
        """Get all birthdays sorted by days until occurrence.

        Args:
            guild: Discord guild for member lookup
            reference_date: Date to calculate from (typically today)
            logger: Logger for warnings

        Returns:
            List of birthday entries sorted by days_until

        """
        entries: list[BirthdayListEntry] = []

        for user_id, user in self.users.items():
            if not user.has_birthday():
                continue

            days_until = calculate_days_until_birthday(user.birthday, reference_date)
            if days_until is None:
                logger.warning(
                    f"Invalid birthday format for user {user_id}: {user.birthday}"
                )
                continue

            formatted_date = format_birthday_date(user.birthday)
            if formatted_date is None:
                logger.warning(
                    f"Could not format birthday for user {user_id}: {user.birthday}"
                )
                continue

            member = await safe_fetch_member(guild, user_id, logger)
            display_name = member.mention if member else user.name

            entries.append(
                {
                    "days_until": days_until,
                    "date": formatted_date,
                    "name": display_name,
                    "user_id": user_id,
                }
            )

        entries.sort(key=lambda x: x["days_until"])
        return entries

    def to_dict(self) -> BirthdayGuildDict:
        return {
            "Server_name": self.server_name,
            "Channel_id": str(self.channel_id),
            "Users": {str(uid): u.to_dict() for uid, u in self.users.items()},
            "Birthday_role": str(self.birthday_role_id)
            if self.birthday_role_id is not None
            else None,
        }

    @classmethod
    def from_dict(cls, guild_id: int, data: BirthdayGuildDict) -> Self:
        users_data = data["Users"]
        users = {
            int(uid): BirthdayUser.from_dict(int(uid), data)
            for uid, data in users_data.items()
        }
        birthday_role_raw = data.get("Birthday_role")
        birthday_role_id = (
            int(birthday_role_raw)
            if birthday_role_raw and birthday_role_raw.isdigit()
            else None
        )
        return cls(
            guild_id=guild_id,
            server_name=data["Server_name"],
            channel_id=int(data["Channel_id"]),
            users=users,
            birthday_role_id=birthday_role_id,
        )


def create_birthday_list_embed(
    guild_name: str,
    entries: list[BirthdayListEntry],
    max_field_length: int = 1024,
) -> discord.Embed:
    """Create embed for birthday list display.

    Args:
        guild_name: Name of the guild
        entries: Sorted list of birthday entries
        max_field_length: Maximum characters per embed field

    Returns:
        Discord embed with birthday list

    """
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
    def __init__(self) -> None:
        self._cache: dict[int, BirthdayGuildConfig] = {}
        self._file_mtime_ns: int | None = None

    def _current_file_mtime_ns(self) -> int | None:
        try:
            return config.BIRTHDAY_FILE.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _refresh_if_file_changed(self) -> None:
        mtime = self._current_file_mtime_ns()
        if mtime != self._file_mtime_ns:
            self._cache.clear()
            self._file_mtime_ns = mtime

    def get_guild_config(self, guild_id: int) -> BirthdayGuildConfig | None:
        """Get guild birthday configuration.

        Looks up the guild configuration in the cache and the birthday file.
        If not found, returns None.

        Args:
            guild_id: Discord guild ID

        Returns:
            GuildBirthdayConfig if exists, None otherwise

        """
        self._refresh_if_file_changed()
        if guild_id in self._cache:
            return self._cache[guild_id]

        raw_data = get_json(config.BIRTHDAY_FILE)
        if not isinstance(raw_data, dict):
            return None

        guild_raw = raw_data.get(str(guild_id))
        if guild_raw is None:
            return None

        guild_config = BirthdayGuildConfig.from_dict(guild_id, guild_raw)
        self._cache[guild_id] = guild_config
        return guild_config

    def get_or_create_guild_config(
        self, guild_id: int, server_name: str, channel_id: int
    ) -> BirthdayGuildConfig:
        """Get or create guild birthday configuration.

        If the guild configuration exists, return the existing configuration.
        Otherwise, create a new configuration with the given parameters
        and store it in the cache.
        """
        existing = self.get_guild_config(guild_id)
        if existing is not None:
            return existing
        new_config = BirthdayGuildConfig(guild_id, server_name, channel_id)
        self._cache[guild_id] = new_config
        return new_config

    def save_guild_config(self, guild_config: BirthdayGuildConfig) -> None:
        """Save guild birthday configuration to file.

        Args:
            guild_config: BirthdayGuildConfig instance to save

        """
        self._cache[guild_config.guild_id] = guild_config
        raw_data = get_json(config.BIRTHDAY_FILE) or {}
        raw_data[str(guild_config.guild_id)] = guild_config.to_dict()
        save_json(config.BIRTHDAY_FILE, raw_data)
        self._file_mtime_ns = self._current_file_mtime_ns()

    def delete_guild_config(self, guild_id: int) -> bool:
        """Delete guild birthday configuration from cache and file.

        Args:
            guild_id: Discord guild ID

        Returns:
            True if the configuration was found and deleted, False otherwise

        """
        self._refresh_if_file_changed()
        if guild_id in self._cache:
            del self._cache[guild_id]
        raw_data = get_json(config.BIRTHDAY_FILE) or {}
        if str(guild_id) in raw_data:
            del raw_data[str(guild_id)]
            save_json(config.BIRTHDAY_FILE, raw_data)
            self._file_mtime_ns = self._current_file_mtime_ns()
            return True
        return False

    def get_all_guild_ids(self) -> list[int]:
        """Get a list of all guild IDs that have birthday configurations.

        Returns:
            A list of guild IDs as integers.

        """
        self._refresh_if_file_changed()
        raw_data = get_json(config.BIRTHDAY_FILE) or {}
        return [int(k) for k in raw_data.keys()]

    def clear_cache(self) -> None:
        """Clear the cache of guild birthday configurations.

        It's is used to clear the internal cache of guild birthday configurations.
        It is useful when the cache needs to be updated, such as when the birthday
        file is updated externally.

        """
        self._cache.clear()


birthday_manager = BirthdayManager()
