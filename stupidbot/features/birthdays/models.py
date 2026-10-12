"""Birthday state and numeric calendar projections without Discord objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .dates import (
    DATE_FORMAT,
    calculate_days_until_birthday,
    format_storage_date,
    is_birthday_today,
)


@dataclass(frozen=True, slots=True)
class UpcomingBirthday:
    user_id: int
    day: int
    month: int
    days_until: int


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
            return datetime.strptime(self.birthday, DATE_FORMAT).date()
        except ValueError:
            return None

    def was_congratulated_today(self, today: date) -> bool:
        today_str = format_storage_date(today)
        return today_str in self.was_congrats

    def add_congratulation(self, congratulation_date: date) -> None:
        date_str = format_storage_date(congratulation_date)
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

    def get_birthdays_today(self, today: date) -> list[BirthdayUser]:
        """Return users due for a congratulation on ``today``.

        Already-congratulated users are excluded. Feb. 29 handling follows
        :func:`.dates.is_birthday_today`.
        """
        return [
            user
            for user in self.users.values()
            if is_birthday_today(user.birthday, today)
            and not user.was_congratulated_today(today)
        ]

    def upcoming_birthdays(self, reference_date: date) -> list[UpcomingBirthday]:
        """Return numeric rows in the original stable birthday order."""
        entries: list[UpcomingBirthday] = []
        for user_id, user in self.users.items():
            birthday = user.birth_date()
            if birthday is None:
                continue
            days_until = calculate_days_until_birthday(user.birthday, reference_date)
            if days_until is None:
                continue
            entries.append(
                UpcomingBirthday(user_id, birthday.day, birthday.month, days_until)
            )
        entries.sort(key=lambda entry: entry.days_until)
        return entries
