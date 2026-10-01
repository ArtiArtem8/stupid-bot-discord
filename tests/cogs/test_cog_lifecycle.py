"""Regression tests for cog-owned background loops."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from api.birthday import birthday_manager
from cogs.birthday_cog import BirthdayCog
from cogs.guild_monitor_cog import ServerMonitorCog


class TestCogLoopLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_birthday_unload_waits_for_timer_cleanup(self) -> None:
        entered = asyncio.Event()
        cancelling = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        cog = BirthdayCog(bot)

        async def guild_ids() -> list[int]:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()
                finished.set()
            return []

        with patch.object(
            birthday_manager, "get_all_guild_ids", new=AsyncMock(side_effect=guild_ids)
        ):
            await cog.cog_load()
            unloading: asyncio.Task[None] | None = None
            try:
                await entered.wait()
                unloading = asyncio.create_task(cog.cog_unload())
                await cancelling.wait()
                self.assertFalse(unloading.done())
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                if unloading is not None:
                    await unloading
                if task := cog.birthday_timer.get_task():
                    await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(finished.is_set())
        self.assertFalse(cog.birthday_timer.is_running())

    async def test_birthday_loop_uses_cog_lifecycle(self) -> None:
        cog = BirthdayCog(MagicMock())
        loop = cog.birthday_timer
        self.assertIsNone(loop.get_task())

        with patch.object(loop, "start") as start:
            with patch.object(loop, "is_running", side_effect=(False, True)):
                await cog.cog_load()
                await cog.cog_load()
            start.assert_called_once()

        with (
            patch.object(loop, "is_running", return_value=True),
            patch.object(loop, "cancel") as cancel,
        ):
            await cog.cog_unload()
        cancel.assert_called_once()

    async def test_guild_monitor_loop_uses_cog_lifecycle(self) -> None:
        cog = ServerMonitorCog(MagicMock())
        loop = cog.cleanup_task
        self.assertIsNone(loop.get_task())

        with patch.object(loop, "start") as start:
            with patch.object(loop, "is_running", side_effect=(False, True)):
                await cog.cog_load()
                await cog.cog_load()
            start.assert_called_once()

        with (
            patch.object(loop, "is_running", return_value=True),
            patch.object(loop, "cancel") as cancel,
        ):
            await cog.cog_unload()
        cancel.assert_called_once()
