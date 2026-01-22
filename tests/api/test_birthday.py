"""Tests for birthday helpers and manager workflows.
Covers parsing, member fetch fallbacks, and config CRUD outcomes.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
from api.birthday import BirthdayManager, parse_birthday, safe_fetch_member
from api.birthday_models import BirthdayGuildConfig


class TestBirthdayHelpers(unittest.IsolatedAsyncioTestCase):
    def test_parse_birthday_accepts_config_format(self) -> None:
        dt = datetime(2000, 1, 2)
        src = dt.strftime(config.DATE_FORMAT)
        self.assertEqual(parse_birthday(src), src)

    def test_parse_birthday_accepts_iso(self) -> None:
        expected = datetime(2000, 1, 2).strftime(config.DATE_FORMAT)
        self.assertEqual(parse_birthday("2000-01-02"), expected)

    async def test_safe_fetch_member_returns_cached(self) -> None:
        member = object()

        def get_member(_: int) -> object:
            return member

        guild = SimpleNamespace(
            get_member=get_member,
            fetch_member=AsyncMock(),
        )
        res = await safe_fetch_member(guild, 1)
        self.assertIs(res, member)
        guild.fetch_member.assert_not_awaited()

    async def test_safe_fetch_member_retries_on_5xx(self) -> None:
        member = object()

        class FakeHTTPException(Exception):
            def __init__(self, status: int):
                super().__init__()
                self.status = status

        def get_member(_: int) -> None:
            return None

        guild = SimpleNamespace(
            get_member=get_member,
            fetch_member=AsyncMock(side_effect=[FakeHTTPException(500), member]),
        )

        with (
            patch("api.birthday.discord.HTTPException", FakeHTTPException),
            patch("api.birthday.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            res = await safe_fetch_member(guild, 1)

        self.assertIs(res, member)
        sleep_mock.assert_awaited_once()


class TestBirthdayManager(unittest.IsolatedAsyncioTestCase):
    async def test_get_or_create_saves_when_missing(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        mgr = BirthdayManager(repo)

        cfg = await mgr.get_or_create_guild_config(1, "S", 123)

        self.assertIsInstance(cfg, BirthdayGuildConfig)
        repo.save.assert_awaited_once()

    async def test_delete_returns_false_when_missing(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        mgr = BirthdayManager(repo)

        ok = await mgr.delete_guild_config(1)

        self.assertFalse(ok)
        repo.delete.assert_not_awaited()
