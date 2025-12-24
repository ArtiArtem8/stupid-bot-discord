from __future__ import annotations

import copy
import logging
import unittest
from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import Mock

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.birthday_repository import BirthdayRepository
from utils import calculate_days_until_birthday


class FakeAsyncJsonFileStore:
    """In-memory async store to avoid filesystem in tests."""

    def __init__(self, initial_data: dict[str, Any] | None = None) -> None:
        self._data = copy.deepcopy(initial_data or {})

    async def read(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    async def update(self, updater: Callable[[Any], None]) -> None:
        data = copy.deepcopy(self._data)
        updater(data)
        self._data = data


class TestBirthdayGuildConfig(unittest.IsolatedAsyncioTestCase):
    """Test cases for BirthdayGuildConfig methods."""

    async def test_get_sorted_birthday_list_empty_users(self):
        """Test get_sorted_birthday_list with no users."""
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)
        mock_guild = Mock()
        mock_logger = Mock()

        entries = await config.get_sorted_birthday_list(
            mock_guild, date(2025, 1, 1), mock_logger
        )

        self.assertEqual(entries, [])

    async def test_get_sorted_birthday_list_sorting(self):
        """Test that birthdays are sorted by proximity to reference date."""
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)

        # Reference date: Jan 1st
        ref_date = date(2025, 1, 1)

        # Alice: Jan 5th (4 days away) - Should be first
        user1 = BirthdayUser(1, "Alice", "05-01-1990")
        # Bob: Feb 1st (31 days away) - Should be second
        user2 = BirthdayUser(2, "Bob", "01-02-1990")
        # Charlie: Dec 31st (364 days away) - Should be last
        user3 = BirthdayUser(3, "Charlie", "31-12-1990")

        config.users = {1: user1, 2: user2, 3: user3}

        mock_guild = Mock()
        mock_guild.get_member.return_value = None
        mock_logger = Mock()

        entries = await config.get_sorted_birthday_list(
            mock_guild, ref_date, mock_logger
        )

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["name"], "Alice")
        self.assertEqual(entries[1]["name"], "Bob")
        self.assertEqual(entries[2]["name"], "Charlie")

        # Verify days_until calculation roughly
        self.assertEqual(entries[0]["days_until"], 4)

    async def test_get_sorted_birthday_list_discord_member_name(self):
        """Test that method prefers Discord nickname over stored name."""
        config = BirthdayGuildConfig(1, "Test", 999)
        user = BirthdayUser(10, "StoredName", "01-01-2000")
        config.users = {10: user}

        mock_guild = Mock()
        mock_member = Mock()
        mock_member.display_name = "DiscordNick"
        mock_guild.get_member.return_value = mock_member

        entries = await config.get_sorted_birthday_list(
            mock_guild, date(2025, 1, 1), Mock()
        )

        self.assertEqual(entries[0]["name"], "DiscordNick")

    def test_get_birthdays_today(self):
        """Test filtering users who have a birthday today."""
        config = BirthdayGuildConfig(1, "Test", 999)

        today = date(2025, 5, 15)

        u1 = BirthdayUser(1, "BdayBoy", "15-05-1990")
        u2 = BirthdayUser(2, "NotToday", "16-05-1990")
        u3 = BirthdayUser(3, "Done", "15-05-1990")
        u3.add_congratulation(today)

        config.users = {1: u1, 2: u2, 3: u3}

        results = config.get_birthdays_today(today)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].user_id, 1)

    async def test_leap_year_birthday_handling(self):
        """Test calculation of birthdays for leap year babies (Feb 29)."""
        config = BirthdayGuildConfig(1, "Test", 999)
        mock_guild = Mock()
        mock_guild.get_member.return_value = None
        mock_logger = Mock()

        # User born on Feb 29, 2000 (Leap Year)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        # Scenario 1: Non-leap year (2025)
        # Birthday should map to Feb 28 or Mar 1 depending on logic (std usually Mar 1)
        # Feb 28 2025 is NOT a leap day.
        # Note: Your utils.birthday_utils logic determines the exact day.
        # This test ensures it doesn't crash and returns a valid positive integer.
        ref_date_non_leap = date(2025, 1, 1)
        entries_2025 = await config.get_sorted_birthday_list(
            mock_guild, ref_date_non_leap, mock_logger
        )
        self.assertEqual(len(entries_2025), 1)
        self.assertTrue(entries_2025[0]["days_until"] > 0)
        # In 2025 (non-leap), Feb 29 doesn't exist.
        # Most simple algos push it to Mar 1 (Day 60). Jan 1 is Day 1. Diff ~59 days.

        # Scenario 2: Leap year (2028)
        # Birthday should exist exactly on Feb 29
        ref_date_leap = date(2028, 1, 1)
        entries_2028 = await config.get_sorted_birthday_list(
            mock_guild, ref_date_leap, mock_logger
        )
        self.assertEqual(len(entries_2028), 1)
        # Feb 29 is the 60th day of 2028. Jan 1 is 1st. 60 - 1 = 59 days away.
        self.assertEqual(entries_2028[0]["days_until"], 59)

    def test_get_birthdays_today_leap_year(self):
        """Test filtering leap year birthdays on actual leap day and non-leap years."""
        config = BirthdayGuildConfig(1, "Test", 999)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        leap_day = date(2024, 2, 29)
        matches = config.get_birthdays_today(leap_day)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "LeapBaby")

        non_leap_day = date(2025, 2, 28)
        matches_strict = config.get_birthdays_today(non_leap_day)
        self.assertEqual(len(matches_strict), 1)

    async def test_leap_year_birthday_handling_feb28_non_leap(self):
        """Test that Feb 29 birthday is celebrated on Feb 28 in non-leap years."""
        config = BirthdayGuildConfig(1, "Test", 999)

        # User born on Feb 29, 2000
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        # Date: Feb 28, 2025 (Non-Leap Year)
        today_non_leap = date(2025, 2, 28)

        matches = config.get_birthdays_today(today_non_leap)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "LeapBaby")

    async def test_leap_year_birthday_handling_feb28_leap(self):
        """Test that Feb 29 birthday is NOT celebrated on Feb 28 in leap years."""
        config = BirthdayGuildConfig(1, "Test", 999)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        # Date: Feb 28, 2024 (Leap Year) - Should wait for Feb 29
        today_leap_28 = date(2024, 2, 28)

        matches = config.get_birthdays_today(today_leap_28)
        self.assertEqual(len(matches), 0)

    async def test_leap_year_birthday_handling_feb29_leap(self):
        """Test that Feb 29 birthday is celebrated on Feb 29 in leap years."""
        config = BirthdayGuildConfig(1, "Test", 999)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        # Date: Feb 29, 2024 (Leap Year)
        today_leap_29 = date(2024, 2, 29)

        matches = config.get_birthdays_today(today_leap_29)
        self.assertEqual(len(matches), 1)

    async def test_calculate_days_until_birthday_leap_logic(self):
        """Test calculation of days until birthday with leap logic."""
        bday_str = "29-02-2000"

        ref_date = date(2025, 1, 1)
        days = calculate_days_until_birthday(bday_str, ref_date)
        self.assertEqual(days, 58)
        ref_date_leap = date(2024, 1, 1)
        days_leap = calculate_days_until_birthday(bday_str, ref_date_leap)
        self.assertEqual(days_leap, 59)


class TestBirthdayRepository(unittest.IsolatedAsyncioTestCase):
    """Test cases for BirthdayRepository CRUD operations."""

    def setUp(self):
        self.store = FakeAsyncJsonFileStore()
        self.repo = BirthdayRepository(self.store)  # type: ignore

    async def test_save_and_get_guild(self):
        """Test saving a guild config and retrieving it."""
        config = BirthdayGuildConfig(
            guild_id=123, server_name="MyServer", channel_id=456, birthday_role_id=789
        )
        user = BirthdayUser(1, "User", "01-01-2000")
        config.users[1] = user

        await self.repo.save(config)

        # Fetch back
        loaded = await self.repo.get(123)

        self.assertIsNotNone(loaded)
        assert loaded is not None  # noqa: S101
        self.assertEqual(loaded.server_name, "MyServer")
        self.assertEqual(loaded.channel_id, 456)
        self.assertEqual(loaded.birthday_role_id, 789)
        self.assertIn(1, loaded.users)
        self.assertEqual(loaded.users[1].name, "User")

    async def test_get_nonexistent_returns_none(self):
        """Test get returns None for missing guild."""
        result = await self.repo.get(99999)
        self.assertIsNone(result)

    async def test_get_all(self):
        """Test retrieving all valid guild configs."""
        c1 = BirthdayGuildConfig(1, "G1", 100)
        c2 = BirthdayGuildConfig(2, "G2", 200)

        await self.repo.save(c1)
        await self.repo.save(c2)

        all_guilds = await self.repo.get_all()

        self.assertEqual(len(all_guilds), 2)
        ids = {g.guild_id for g in all_guilds}
        self.assertEqual(ids, {1, 2})

    async def test_delete(self):
        """Test deleting a guild config."""
        c1 = BirthdayGuildConfig(1, "G1", 100)
        await self.repo.save(c1)

        await self.repo.delete(1)

        result = await self.repo.get(1)
        self.assertIsNone(result)

    async def test_get_all_handles_corrupt_data(self):
        """Test that get_all skips invalid entries without crashing."""
        bad_data: dict[str, Any] = {
            "1": {"Server_name": "Valid", "Channel_id": "1", "Users": {}},
            "2": "Not a dict",
            "not_an_int": {},
            "3": {},  # Missing required fields (should trigger exception)
        }
        self.repo = BirthdayRepository(FakeAsyncJsonFileStore(bad_data))  # type: ignore

        # Suppress logging during this test
        logging.disable(logging.ERROR)
        try:
            results = await self.repo.get_all()
        finally:
            logging.disable(logging.NOTSET)

        # Only guild "1" is valid
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].guild_id, 1)


if __name__ == "__main__":
    unittest.main()
