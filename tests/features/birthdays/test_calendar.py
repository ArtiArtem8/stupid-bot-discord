"""Birthday calendar queries and leap-year behavior."""

from __future__ import annotations

import unittest
from datetime import date

from stupidbot.features.birthdays.models import BirthdayGuildConfig, BirthdayUser
from stupidbot.features.birthdays.presentation import build_birthday_list
from stupidbot.i18n.policy import Language


class TestBirthdayGuildConfig(unittest.TestCase):
    def test_translation_does_not_change_leap_day_occurrence_or_saved_name(
        self,
    ) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)
        config.users = {1: BirthdayUser(1, "Алексей", "29-02-2000")}
        ru = build_birthday_list(config, {}, date(2025, 2, 28), language=Language.RU)
        en = build_birthday_list(config, {}, date(2025, 2, 28), language=Language.EN)
        ko = build_birthday_list(config, {}, date(2025, 2, 28), language=Language.KO)
        self.assertEqual(ru[0]["date"], "29 февраля")
        self.assertEqual(en[0]["date"], "February 29")
        self.assertEqual(ru[0]["days_until"], en[0]["days_until"])
        self.assertEqual(en[0]["name"], "Алексей")
        self.assertEqual(ko[0]["date"], "2월 29일")
        self.assertEqual(ko[0]["days_until"], ru[0]["days_until"])
        self.assertEqual(ko[0]["name"], "Алексей")

    def test_get_sorted_birthday_list_empty_users(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)

        entries = build_birthday_list(
            config,
            {},
            date(2025, 1, 1),
        )

        self.assertEqual(entries, [])

    def test_get_sorted_birthday_list_sorting(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)

        ref_date = date(2025, 1, 1)

        user1 = BirthdayUser(1, "Alice", "05-01-1990")
        user2 = BirthdayUser(2, "Bob", "01-02-1990")
        user3 = BirthdayUser(3, "Charlie", "31-12-1990")

        config.users = {1: user1, 2: user2, 3: user3}

        entries = build_birthday_list(
            config,
            {},
            ref_date,
        )
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["name"], "Alice")
        self.assertEqual(entries[1]["name"], "Bob")
        self.assertEqual(entries[2]["name"], "Charlie")

        self.assertEqual(entries[0]["days_until"], 4)

    def test_get_sorted_birthday_list_discord_member_name(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        user = BirthdayUser(10, "StoredName", "01-01-2000")
        config.users = {10: user}

        entries = build_birthday_list(config, {10: "DiscordNick"}, date(2025, 1, 1))

        self.assertEqual(entries[0]["name"], "DiscordNick")

    def test_due_excludes_other_dates_and_congratulated_users(self) -> None:
        today = date(2025, 5, 15)
        done = BirthdayUser(3, "Done", "15-05-1990")
        done.add_congratulation(today)

        self.assertTrue(BirthdayUser(1, "BdayBoy", "15-05-1990").is_due(today))
        self.assertFalse(BirthdayUser(2, "NotToday", "16-05-1990").is_due(today))
        self.assertFalse(done.is_due(today))

    def test_leap_year_birthday_handling(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)

        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        ref_date_non_leap = date(2025, 1, 1)
        entries_2025 = build_birthday_list(
            config,
            {},
            ref_date_non_leap,
        )
        self.assertEqual(len(entries_2025), 1)
        self.assertGreater(entries_2025[0]["days_until"], 0)

        ref_date_leap = date(2028, 1, 1)
        entries_2028 = build_birthday_list(
            config,
            {},
            ref_date_leap,
        )
        self.assertEqual(len(entries_2028), 1)
        self.assertEqual(entries_2028[0]["days_until"], 59)

    def test_leap_day_is_due_on_feb_28_only_in_non_leap_years(self) -> None:
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")

        self.assertTrue(leap_user.is_due(date(2024, 2, 29)))
        self.assertFalse(leap_user.is_due(date(2024, 2, 28)))
        self.assertTrue(leap_user.is_due(date(2025, 2, 28)))
