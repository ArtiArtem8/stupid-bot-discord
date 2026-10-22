"""Birthday state and numeric calendar projections without Discord objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from .dates import (
    DATE_FORMAT,
    day_start,
    format_storage_date,
    is_birthday_today,
    next_celebration,
)


@dataclass(frozen=True, slots=True)
class UpcomingBirthday:
    """The next celebration in the member's timezone.

    ``today`` means the member's local celebration day is in progress,
    regardless of whether a congratulation was already sent. ``starts_at`` is
    the UTC start of ``celebration`` in that timezone.
    """

    user_id: int
    celebration: date
    starts_at: datetime
    today: bool


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

    def is_due(self, today: date) -> bool:
        """Return whether ``today`` is an uncongratulated celebration day.

        ``today`` is the member's local calendar date. Feb. 29 handling follows
        :func:`.dates.is_birthday_today`.
        """
        return is_birthday_today(
            self.birthday, today
        ) and not self.was_congratulated_today(today)

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

    def upcoming_birthdays(
        self, local_times: Mapping[int, datetime]
    ) -> list[UpcomingBirthday]:
        """Return celebrations in progress, then future ones by UTC start.

        ``local_times`` holds the current instant in each member's timezone.
        In-progress celebrations keep the saved member order.
        """
        current: list[UpcomingBirthday] = []
        future: list[UpcomingBirthday] = []
        for user_id, user in self.users.items():
            birthday = user.birth_date()
            if birthday is None:
                continue
            now = local_times[user_id]
            if now.tzinfo is None:
                raise ValueError("Member local times must be timezone-aware")
            celebration = next_celebration(birthday, now.date())
            entry = UpcomingBirthday(
                user_id,
                celebration,
                day_start(celebration, now.tzinfo),
                celebration == now.date(),
            )
            if entry.today:
                current.append(entry)
            else:
                future.append(entry)
        future.sort(key=lambda entry: entry.starts_at)
        return current + future
