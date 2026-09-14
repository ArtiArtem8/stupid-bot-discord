"""Tests for birthday repository storage and queries."""

from __future__ import annotations

import asyncio
import logging
import unittest
from datetime import date
from typing import override
from unittest.mock import Mock

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.birthday_repository import BirthdayRepository
from tests.repositories.fakes import InMemoryJsonStore
from utils.birthday_utils import calculate_days_until_birthday
from utils.json_types import JsonObject


class TestBirthdayGuildConfig(unittest.IsolatedAsyncioTestCase):
    async def test_get_sorted_birthday_list_empty_users(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)
        mock_guild = Mock()
        mock_logger = Mock()

        entries = await config.get_sorted_birthday_list(
            mock_guild, date(2025, 1, 1), mock_logger
        )

        self.assertEqual(entries, [])

    async def test_get_sorted_birthday_list_sorting(self) -> None:
        config = BirthdayGuildConfig(guild_id=123, server_name="Test", channel_id=999)

        ref_date = date(2025, 1, 1)

        user1 = BirthdayUser(1, "Alice", "05-01-1990")
        user2 = BirthdayUser(2, "Bob", "01-02-1990")
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

        self.assertEqual(entries[0]["days_until"], 4)

    async def test_get_sorted_birthday_list_discord_member_name(self) -> None:
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

    def test_get_birthdays_today(self) -> None:
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

    async def test_leap_year_birthday_handling(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        mock_guild = Mock()
        mock_guild.get_member.return_value = None
        mock_logger = Mock()

        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        ref_date_non_leap = date(2025, 1, 1)
        entries_2025 = await config.get_sorted_birthday_list(
            mock_guild, ref_date_non_leap, mock_logger
        )
        self.assertEqual(len(entries_2025), 1)
        self.assertGreater(entries_2025[0]["days_until"], 0)

        ref_date_leap = date(2028, 1, 1)
        entries_2028 = await config.get_sorted_birthday_list(
            mock_guild, ref_date_leap, mock_logger
        )
        self.assertEqual(len(entries_2028), 1)
        self.assertEqual(entries_2028[0]["days_until"], 59)

    def test_get_birthdays_today_leap_year(self) -> None:
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

    async def test_leap_year_birthday_handling_feb28_non_leap(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)

        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        today_non_leap = date(2025, 2, 28)

        matches = config.get_birthdays_today(today_non_leap)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "LeapBaby")

    async def test_leap_year_birthday_handling_feb28_leap(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        today_leap_28 = date(2024, 2, 28)

        matches = config.get_birthdays_today(today_leap_28)
        self.assertEqual(len(matches), 0)

    async def test_leap_year_birthday_handling_feb29_leap(self) -> None:
        config = BirthdayGuildConfig(1, "Test", 999)
        leap_user = BirthdayUser(1, "LeapBaby", "29-02-2000")
        config.users = {1: leap_user}

        today_leap_29 = date(2024, 2, 29)

        matches = config.get_birthdays_today(today_leap_29)
        self.assertEqual(len(matches), 1)

    async def test_calculate_days_until_birthday_leap_logic(self) -> None:
        bday_str = "29-02-2000"

        ref_date = date(2025, 1, 1)
        days = calculate_days_until_birthday(bday_str, ref_date)
        self.assertEqual(days, 58)
        ref_date_leap = date(2024, 1, 1)
        days_leap = calculate_days_until_birthday(bday_str, ref_date_leap)
        self.assertEqual(days_leap, 59)


class TestBirthdayRepository(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.store = InMemoryJsonStore()
        self.repo = BirthdayRepository(self.store)

    async def test_save_and_get_guild(self) -> None:
        config = BirthdayGuildConfig(
            guild_id=123, server_name="MyServer", channel_id=456, birthday_role_id=789
        )
        user = BirthdayUser(1, "User", "01-01-2000")
        config.users[1] = user

        await self.repo.save(config)

        loaded = await self.repo.get(123)

        self.assertIsNotNone(loaded)
        if loaded is None:
            self.fail("expected saved birthday guild config")
        self.assertEqual(loaded.server_name, "MyServer")
        self.assertEqual(loaded.channel_id, 456)
        self.assertEqual(loaded.birthday_role_id, 789)
        self.assertIn(1, loaded.users)
        self.assertEqual(loaded.users[1].name, "User")

    async def test_get_nonexistent_returns_none(self) -> None:
        result = await self.repo.get(99999)
        self.assertIsNone(result)

    async def test_get_all(self) -> None:
        c1 = BirthdayGuildConfig(1, "G1", 100)
        c2 = BirthdayGuildConfig(2, "G2", 200)

        await self.repo.save(c1)
        await self.repo.save(c2)

        all_guilds = await self.repo.get_all()

        self.assertEqual(len(all_guilds), 2)
        ids = {g.guild_id for g in all_guilds}
        self.assertEqual(ids, {1, 2})

    async def test_delete(self) -> None:
        c1 = BirthdayGuildConfig(1, "G1", 100)
        await self.repo.save(c1)

        await self.repo.delete(1)

        result = await self.repo.get(1)
        self.assertIsNone(result)

    async def test_get_all_handles_corrupt_data(self) -> None:
        bad_data: JsonObject = {
            "1": {"Server_name": "Valid", "Channel_id": "1", "Users": {}},
            "2": "Not a dict",
            "not_an_int": {},
            "3": {},  # Missing required fields (should trigger exception)
        }
        self.repo = BirthdayRepository(InMemoryJsonStore(bad_data))

        logging.disable(logging.ERROR)
        try:
            results = await self.repo.get_all()
        finally:
            logging.disable(logging.NOTSET)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].guild_id, 1)

    async def test_concurrent_user_updates_preserve_both_birthdays(self) -> None:
        await asyncio.gather(
            self.repo.set_user_birthday(123, "Guild", 456, 1, "One", "01-01-2000"),
            self.repo.set_user_birthday(123, "Guild", 456, 2, "Two", "02-02-2000"),
        )

        loaded = await self.repo.get(123)

        self.assertIsNotNone(loaded)
        if loaded is None:
            self.fail("expected saved birthday guild config")
        self.assertEqual(set(loaded.users), {1, 2})

    async def test_semantic_update_rejects_invalid_existing_guild(self) -> None:
        store = InMemoryJsonStore({"123": {}})
        repo = BirthdayRepository(store)

        with self.assertRaises(ValueError):
            await repo.set_user_birthday(123, "Guild", 456, 1, "One", "01-01-2000")

        self.assertEqual(store.data, {"123": {}})

    async def test_clear_missing_birthday_is_noop(self) -> None:
        await self.repo.save(BirthdayGuildConfig(123, "Guild", 456))
        calls_before = self.store.update_calls

        guild_exists, cleared = await self.repo.clear_user_birthday(123, 1)

        self.assertTrue(guild_exists)
        self.assertFalse(cleared)
        self.assertEqual(self.store.update_calls, calls_before + 1)
