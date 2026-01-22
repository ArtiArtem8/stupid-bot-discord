"""Tests for blocking manager operations.
Covers block/unblock flows and blocked-state lookup behavior.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from api.blocking import BlockManager
from api.blocking_models import BlockedUser


class TestBlockManager(unittest.IsolatedAsyncioTestCase):
    async def test_is_user_blocked_false_when_missing(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        mgr = BlockManager(repo)

        blocked = await mgr.is_user_blocked(guild_id=1, user_id=2)

        self.assertFalse(blocked)
        repo.get.assert_awaited_once_with((1, 2))

    async def test_is_user_blocked_true_when_found(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = BlockedUser(
            user_id=2,
            current_username="u",
            current_global_name=None,
            blocked=True,
        )
        mgr = BlockManager(repo)

        blocked = await mgr.is_user_blocked(guild_id=1, user_id=2)

        self.assertTrue(blocked)

    async def test_block_user_creates_or_updates_and_saves(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        mgr = BlockManager(repo)

        member = SimpleNamespace(
            id=10,
            display_name="Nick",
            name="Global",
        )

        user = await mgr.block_user(guild_id=99, target=member, admin_id=7, reason="r")

        self.assertTrue(user.blocked)
        repo.save.assert_awaited()

    async def test_unblock_user_when_not_blocked_does_not_toggle(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = BlockedUser(
            user_id=10,
            current_username="Nick",
            current_global_name="Global",
            blocked=False,
        )
        mgr = BlockManager(repo)

        member = SimpleNamespace(id=10, display_name="Nick", name="Global")
        user = await mgr.unblock_user(
            guild_id=99, target=member, admin_id=7, reason="r"
        )

        self.assertFalse(user.blocked)
