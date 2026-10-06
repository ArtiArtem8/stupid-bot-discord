"""The manager sends each block transition through one repository operation."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from api.blocking import BlockManager
from repositories.sqlite_blocking_repository import SQLiteBlockingRepository


class TestBlockManager(unittest.IsolatedAsyncioTestCase):
    async def test_access_uses_committed_state_and_propagates_unavailability(
        self,
    ) -> None:
        repository = MagicMock(spec=SQLiteBlockingRepository)
        repository.is_blocked = AsyncMock(return_value=False)
        manager = BlockManager(repository)
        self.assertFalse(await manager.is_user_blocked(1, 2))
        repository.is_blocked.assert_awaited_once_with(1, 2)
        repository.is_blocked.side_effect = OSError("unavailable")
        with self.assertRaises(OSError):
            await manager.is_user_blocked(1, 2)

    async def test_block_and_unblock_each_use_atomic_change(self) -> None:
        repository = MagicMock(spec=SQLiteBlockingRepository)
        repository.change = AsyncMock(return_value=True)
        manager = BlockManager(repository)
        member = MagicMock(spec=discord.Member, id=2, display_name="Nick", name="Name")
        self.assertTrue(await manager.block_user(1, member, 3, "reason"))
        self.assertTrue(await manager.unblock_user(1, member, 3, "reason"))
        self.assertEqual(repository.change.await_count, 2)
        self.assertEqual(
            [call.kwargs["blocked"] for call in repository.change.await_args_list],
            [True, False],
        )
