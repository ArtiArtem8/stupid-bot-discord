"""Tests for birthday model behavior and sorting helpers."""

from __future__ import annotations

import unittest
from datetime import date

from stupidbot.features.birthdays.models import BirthdayGuildConfig, BirthdayUser
from stupidbot.features.birthdays.presentation import build_birthday_list


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


class TestBirthdayGuildConfig(unittest.TestCase):
    def test_sorted_birthday_list_falls_back_to_stored_name(self) -> None:
        cfg = BirthdayGuildConfig(guild_id=1, server_name="S", channel_id=1)
        cfg.users[10] = BirthdayUser(10, "StoredName", "02-01-2000", [])
        entries = build_birthday_list(cfg, {}, date(2025, 1, 1))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "StoredName")
