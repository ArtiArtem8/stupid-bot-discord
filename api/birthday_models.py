from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import NotRequired, Self, TypedDict

import discord

import config
from utils.birthday_utils import calculate_days_until_birthday, format_birthday_date


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

    def to_dict(self) -> BirthdayGuildDict:
        return {
            "Server_name": self.server_name,
            "Channel_id": str(self.channel_id),
            "Users": {str(uid): u.to_dict() for uid, u in self.users.items()},
            # Convert Role ID to string for JSON, handle None
            "Birthday_role": str(self.birthday_role_id)
            if self.birthday_role_id is not None
            else None,
        }

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
        entries: list[BirthdayListEntry] = []
        for user_id, user_data in self.users.items():
            if not user_data.has_birthday():
                continue

            # Calculate days until birthday
            days_until = calculate_days_until_birthday(
                user_data.birthday, reference_date
            )
            if days_until is None:
                continue

            # Format the birthday date
            formatted_date = format_birthday_date(user_data.birthday)
            if not formatted_date:
                continue

            # Get the member name (either from Discord or fallback to stored name)
            member = guild.get_member(user_id)
            display_name = member.display_name if member else user_data.name

            entry: BirthdayListEntry = {
                "days_until": days_until,
                "date": formatted_date,
                "name": display_name,
                "user_id": user_id,
            }
            entries.append(entry)

        # Sort by days until birthday (closest first)
        entries.sort(key=lambda x: x["days_until"])
        return entries

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
