"""Tests for birthday helpers and manager workflows."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import discord

import config
from api.birthday import (
    BirthdayManager,
    create_birthday_list_embed,
    parse_birthday,
    safe_fetch_member,
)
from api.birthday_models import BirthdayListEntry


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
        src = dt.strftime(config.DATE_FORMAT)
        self.assertEqual(parse_birthday(src), src)

    def test_parse_birthday_accepts_iso(self) -> None:
        expected = datetime(2000, 1, 2).strftime(config.DATE_FORMAT)
        self.assertEqual(parse_birthday("2000-01-02"), expected)

    async def test_safe_fetch_member_returns_cached(self) -> None:
        member = object()
        fetch_member = AsyncMock()

        def get_member(_: int) -> object:
            return member

        guild = cast(
            discord.Guild,
            cast(
                object,
                SimpleNamespace(
                    get_member=get_member,
                    fetch_member=fetch_member,
                ),
            ),
        )
        res = await safe_fetch_member(guild, 1)
        self.assertIs(res, member)
        fetch_member.assert_not_awaited()

    async def test_safe_fetch_member_retries_on_5xx(self) -> None:
        member = object()

        class FakeHTTPException(Exception):
            def __init__(self, status: int) -> None:
                super().__init__()
                self.status = status

        def get_member(_: int) -> None:
            return None

        guild = cast(
            discord.Guild,
            cast(
                object,
                SimpleNamespace(
                    get_member=get_member,
                    fetch_member=AsyncMock(
                        side_effect=[FakeHTTPException(500), member]
                    ),
                ),
            ),
        )

        with (
            patch("api.birthday.discord.HTTPException", FakeHTTPException),
            patch("api.birthday.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            res = await safe_fetch_member(guild, 1)

        self.assertIs(res, member)
        sleep_mock.assert_awaited_once()


class TestBirthdayManager(unittest.IsolatedAsyncioTestCase):
    async def test_set_user_birthday_delegates_semantic_mutation(self) -> None:
        repo = AsyncMock()
        mgr = BirthdayManager(repo)

        await mgr.set_user_birthday(1, "S", 123, 10, "User", "01-01-2000")

        repo.set_user_birthday.assert_awaited_once_with(
            1, "S", 123, 10, "User", "01-01-2000"
        )
