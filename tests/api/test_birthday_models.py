"""Tests for birthday model behavior and sorting helpers.
Covers user parsing and guild list ordering with mocked guild data.
"""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.birthday_models import BirthdayGuildConfig, BirthdayUser


class TestBirthdayUser(unittest.TestCase):
    def test_add_congratulation_is_idempotent(self) -> None:
        u = BirthdayUser(user_id=1, name="n", birthday="2000-01-02")
        today = date(2025, 1, 2)

        u.add_congratulation(today)
        u.add_congratulation(today)

        self.assertEqual(len(u.was_congrats), 1)

    def test_birth_date_returns_none_when_invalid(self) -> None:
        u = BirthdayUser(user_id=1, name="n", birthday="bad-date")
        self.assertIsNone(u.birth_date())


class TestBirthdayGuildConfig(unittest.IsolatedAsyncioTestCase):
    async def test_sorted_birthday_list_falls_back_to_stored_name(self) -> None:
        cfg = BirthdayGuildConfig(guild_id=1, server_name="S", channel_id=1)
        cfg.users[10] = BirthdayUser(10, "StoredName", "2000-01-02", [])

        def get_member(_: int) -> object:
            return None

        guild = SimpleNamespace(
            name="S",
            id=1,
            get_member=get_member,
        )

        logger = Mock()

        def fake_days(_: str, _d: date) -> int:
            return 1

        def fake_fmt(_: str) -> str:
            return "02-01-2000"

        with (
            patch("api.birthday_models.calculate_days_until_birthday", fake_days),
            patch("api.birthday_models.format_birthday_date", fake_fmt),
        ):
            entries = await cfg.get_sorted_birthday_list(
                guild, date(2025, 1, 1), logger
            )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "StoredName")
