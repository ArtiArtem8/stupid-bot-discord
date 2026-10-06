from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TypedDict

import discord

import config
from utils.birthday_utils import (
    calculate_days_until_birthday,
    format_birthday_date,
    is_birthday_today,
)


class BirthdayListEntry(TypedDict):
    """One presentation-ready birthday list row."""

    days_until: int
    date: str
    name: str
    user_id: int


@dataclass(frozen=True, slots=True)
class BirthdayDelivery:
    """A durable claim for one calendar-day send using specific saved versions."""

    operation_id: str
    guild_id: int
    user_id: int
    today: date
    settings_version: int
    birthday_version: int


@dataclass
class BirthdayUser:
    """One guild member's birthday and sent-congratulation history."""

    user_id: int
    name: str
    birthday: str
    was_congrats: list[str] = field(default_factory=list[str])
    version: int = 1

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


@dataclass
class BirthdayGuildConfig:
    """Birthday delivery settings and users for one Discord guild."""

    guild_id: int
    server_name: str
    channel_id: int | None
    users: dict[int, BirthdayUser] = field(default_factory=dict[int, BirthdayUser])
    birthday_role_id: int | None = None
    version: int = 1

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
        """Return users due for a congratulation on ``today``.

        Already-congratulated users are excluded. Feb. 29 handling follows
        :func:`utils.birthday_utils.is_birthday_today`.
        """
        return [
            user
            for user in self.users.values()
            if is_birthday_today(user.birthday, today)
            and not user.was_congratulated_today(today)
        ]

    async def get_sorted_birthday_list(
        self, guild: discord.Guild, reference_date: date, logger: logging.Logger
    ) -> list[BirthdayListEntry]:
        """Get all birthdays sorted by closest to the reference date.

        Args:
            guild: Discord guild for fetching members
            reference_date: Date to calculate days until birthday from
            logger: Logger for logging any errors

        Returns:
            List of birthday entries sorted by days until birthday

        """
        logger.info(
            "Generating sorted birthday list for guild %s (%d) with reference date %s",
            guild.name,
            guild.id,
            reference_date,
        )

        entries: list[BirthdayListEntry] = []
        for user_id, user_data in self.users.items():
            if not user_data.has_birthday():
                logger.debug("Skipping user %d: No birthday set", user_id)
                continue

            days_until = calculate_days_until_birthday(
                user_data.birthday, reference_date
            )
            if days_until is None:
                logger.warning(
                    "Skipping user %d: Failed to calculate days until birthday for %s",
                    user_id,
                    user_data.birthday,
                )
                continue

            formatted_date = format_birthday_date(user_data.birthday)
            if not formatted_date:
                logger.warning(
                    "Skipping user %d: Failed to format birthday date %s",
                    user_id,
                    user_data.birthday,
                )
                continue

            member = guild.get_member(user_id)
            if member:
                display_name = member.display_name
                logger.debug(
                    "Found member %d in guild: using display name '%s'",
                    user_id,
                    display_name,
                )
            else:
                display_name = user_data.name
                logger.debug(
                    "Member %d not found in guild: falling back to stored name '%s'",
                    user_id,
                    display_name,
                )

            entry: BirthdayListEntry = {
                "days_until": days_until,
                "date": formatted_date,
                "name": display_name,
                "user_id": user_id,
            }
            entries.append(entry)

        entries.sort(key=lambda x: x["days_until"])
        logger.info(
            "Generated birthday list for guild %s: %d entries found",
            guild.name,
            len(entries),
        )
        return entries
