"""Tests for birthday helpers and manager workflows."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from stupidbot.features.birthdays.dates import DATE_FORMAT, parse_birthday
from stupidbot.features.birthdays.delivery import safe_fetch_member
from stupidbot.features.birthdays.presentation import (
    BirthdayListEntry,
    create_birthday_list_embed,
)


class TestBirthdayHelpers(unittest.IsolatedAsyncioTestCase):
    def test_large_birthday_embed_includes_footer_in_total_budget(self) -> None:
        entries: list[BirthdayListEntry] = [
            {"date": "01-01-2000", "name": "x" * 128, "days_until": 1, "user_id": 1}
            for _ in range(100)
        ]
        embed = create_birthday_list_embed("g" * 256, entries)
        self.assertLessEqual(len(embed), 6000)
        self.assertIn("100", embed.footer.text or "")

    def test_small_years_round_trip_with_four_digits(self) -> None:
        for year in (1, 999, 1000):
            with self.subTest(year=year):
                expected = f"02-01-{year:04d}"
                self.assertEqual(parse_birthday(expected), expected)
                self.assertEqual(parse_birthday(f"{year:04d}-01-02"), expected)

    def test_parse_birthday_accepts_config_format(self) -> None:
        dt = datetime(2000, 1, 2)
        src = dt.strftime(DATE_FORMAT)
        self.assertEqual(parse_birthday(src), src)

    def test_parse_birthday_accepts_iso(self) -> None:
        expected = datetime(2000, 1, 2).strftime(DATE_FORMAT)
        self.assertEqual(parse_birthday("2000-01-02"), expected)

    async def test_safe_fetch_member_returns_cached(self) -> None:
        member = MagicMock(spec=discord.Member)
        guild = MagicMock(spec=discord.Guild)
        guild.get_member.return_value = member
        res = await safe_fetch_member(guild, 1)
        self.assertIs(res, member)
        guild.fetch_member.assert_not_awaited()

    async def test_safe_fetch_member_retries_on_5xx(self) -> None:
        member = MagicMock(spec=discord.Member)
        guild = MagicMock(spec=discord.Guild)
        guild.get_member.return_value = None
        guild.fetch_member.side_effect = [
            discord.HTTPException(MagicMock(status=500, reason="Server error"), ""),
            member,
        ]

        with patch.object(asyncio, "sleep", new=AsyncMock()) as sleep_mock:
            res = await safe_fetch_member(guild, 1)

        self.assertIs(res, member)
        self.assertEqual(guild.fetch_member.await_count, 2)
        sleep_mock.assert_awaited_once_with(2)
