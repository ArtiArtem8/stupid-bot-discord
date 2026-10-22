"""Birthday calendar queries and leap-year behavior."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from stupidbot.features.birthdays.models import BirthdayGuildConfig, BirthdayUser
from stupidbot.features.birthdays.presentation import (
    BirthdayListEntry,
    build_birthday_list,
)
from stupidbot.i18n.policy import Language

MOSCOW = ZoneInfo("Europe/Moscow")
SEOUL = ZoneInfo("Asia/Seoul")


def _list(
    config: BirthdayGuildConfig,
    now: datetime,
    zones: Mapping[int, ZoneInfo] | None = None,
    *,
    language: Language = Language.RU,
) -> list[BirthdayListEntry]:
    zones = zones or {}
    local_times = {
        user_id: now.astimezone(zones.get(user_id, UTC)) for user_id in config.users
    }
    return build_birthday_list(config, {}, local_times, language=language)


class TestBirthdayGuildConfig(unittest.TestCase):
    def test_translation_shows_non_leap_celebration_date_and_saved_name(
        self,
    ) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)
        config.users = {1: BirthdayUser(1, "Алексей", "29-02-2000")}
        now = datetime(2025, 2, 1, tzinfo=UTC)
        ru = _list(config, now, language=Language.RU)
        en = _list(config, now, language=Language.EN)
        ko = _list(config, now, language=Language.KO)
        self.assertEqual(ru[0]["date"], "28 февраля")
        self.assertEqual(en[0]["date"], "February 28")
        self.assertEqual(ko[0]["date"], "2월 28일")
        self.assertEqual(en[0]["name"], "Алексей")
        self.assertEqual(ru[0]["starts_at"], datetime(2025, 2, 28, tzinfo=UTC))
        leap = _list(config, datetime(2028, 2, 1, tzinfo=UTC))
        self.assertEqual(leap[0]["date"], "29 февраля")

    def test_get_sorted_birthday_list_empty_users(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)

        self.assertEqual(_list(config, datetime(2025, 1, 1, tzinfo=UTC)), [])

    def test_future_birthdays_sort_by_start_across_year_end(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)
        config.users = {
            3: BirthdayUser(3, "Charlie", "31-12-1990"),
            1: BirthdayUser(1, "Alice", "05-01-1990"),
            2: BirthdayUser(2, "Bob", "01-02-1990"),
        }

        entries = _list(config, datetime(2025, 1, 1, tzinfo=UTC))

        self.assertEqual(
            [entry["name"] for entry in entries], ["Alice", "Bob", "Charlie"]
        )
        self.assertEqual(entries[0]["starts_at"], datetime(2025, 1, 5, tzinfo=UTC))

    def test_current_celebrations_lead_in_saved_order_and_ignore_history(
        self,
    ) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        sent = BirthdayUser(2, "Sent", "06-10-2000")
        sent.add_congratulation(date(2026, 10, 6))
        config.users = {
            3: BirthdayUser(3, "Soon", "07-10-2000"),
            2: sent,
            1: BirthdayUser(1, "Later", "06-10-2000"),
        }

        entries = _list(config, datetime(2026, 10, 6, 12, tzinfo=UTC))

        self.assertEqual(
            [entry["name"] for entry in entries], ["Sent", "Later", "Soon"]
        )
        self.assertIsNone(entries[0]["starts_at"])
        self.assertIsNone(entries[1]["starts_at"])

    def test_moscow_member_is_upcoming_while_seoul_member_celebrates(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        config.users = {
            1: BirthdayUser(1, "Moscow", "06-10-2000"),
            2: BirthdayUser(2, "Seoul", "06-10-2000"),
        }
        # 20:00 UTC on Oct. 5 is already Oct. 6 in Seoul and 23:00 in Moscow.
        now = datetime(2026, 10, 5, 20, tzinfo=UTC)

        entries = _list(config, now, {1: MOSCOW, 2: SEOUL})

        self.assertEqual([entry["name"] for entry in entries], ["Seoul", "Moscow"])
        self.assertIsNone(entries[0]["starts_at"])
        self.assertEqual(entries[1]["starts_at"], datetime(2026, 10, 5, 21, tzinfo=UTC))
        self.assertEqual(entries[1]["date"], "6 октября")

    def test_finished_local_birthday_counts_toward_next_year(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        config.users = {1: BirthdayUser(1, "Seoul", "06-10-2000")}
        # Oct. 7 has begun in Seoul while Moscow is still on Oct. 6.
        entries = _list(config, datetime(2026, 10, 6, 15, tzinfo=UTC), {1: SEOUL})

        self.assertEqual(entries[0]["starts_at"], datetime(2027, 10, 5, 15, tzinfo=UTC))

    def test_get_sorted_birthday_list_discord_member_name(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        user = BirthdayUser(10, "StoredName", "01-01-2000")
        config.users = {10: user}
        local_times = {10: datetime(2025, 1, 1, tzinfo=UTC)}

        entries = build_birthday_list(config, {10: "DiscordNick"}, local_times)

        self.assertEqual(entries[0]["name"], "DiscordNick")

    def test_due_excludes_other_dates_and_congratulated_users(self) -> None:
        today = date(2025, 5, 15)
        done = BirthdayUser(3, "Done", "15-05-1990")
        done.add_congratulation(today)

        self.assertTrue(BirthdayUser(1, "BdayBoy", "15-05-1990").is_due(today))
        self.assertFalse(BirthdayUser(2, "NotToday", "16-05-1990").is_due(today))
        self.assertFalse(done.is_due(today))

    def test_leap_day_is_due_on_feb_28_only_in_non_leap_years(self) -> None:
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")

        self.assertTrue(leap_user.is_due(date(2024, 2, 29)))
        self.assertFalse(leap_user.is_due(date(2024, 2, 28)))
        self.assertTrue(leap_user.is_due(date(2025, 2, 28)))
