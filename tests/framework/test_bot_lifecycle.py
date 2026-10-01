"""Bot-owned background work completes before Discord shutdown and final save."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

from framework.bot import StupidBot
from framework.cog_loader import CogLoader


class TestBotShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_before_startup_is_idempotent(self) -> None:
        bot = StupidBot()
        await bot.close()
        await bot.close()
        self.assertTrue(bot.is_closed())
        self.assertIsNone(bot.autosave_task.get_task())
        self.assertIsNone(bot.update_activity_task.get_task())

    async def test_partial_startup_failure_still_closes_loaded_resources(self) -> None:
        loader = MagicMock(spec=CogLoader)
        loader.load_cogs.side_effect = RuntimeError("partial load")
        bot = StupidBot(cog_loader=loader)
        with patch.object(commands.Bot, "close", new=AsyncMock()) as discord_close:
            with self.assertRaises(RuntimeError):
                await bot.setup_hook()
            await bot.close()
        loader.close.assert_awaited_once()
        discord_close.assert_awaited_once()

    async def test_cancelled_and_concurrent_close_wait_for_inflight_autosave(
        self,
    ) -> None:
        started = asyncio.Event()
        cancelling = asyncio.Event()
        release = asyncio.Event()
        persisted = asyncio.Event()
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader)

        async def save() -> float:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()
                persisted.set()
            return 0.0

        async def library_close() -> None:
            self.assertTrue(persisted.is_set())
            activity = bot.update_activity_task.get_task()
            self.assertIsNotNone(activity)
            if activity is not None:
                self.assertTrue(activity.done())

        with (
            patch.object(bot, "wait_until_ready", new=AsyncMock()),
            patch.object(bot, "change_presence", new=AsyncMock()),
            patch.object(bot, "save_state", new=AsyncMock(side_effect=save)),
            patch.object(
                commands.Bot, "close", new=AsyncMock(side_effect=library_close)
            ) as discord_close,
        ):
            bot.update_activity_task.start()
            bot.autosave_task.start()
            await started.wait()
            first = asyncio.create_task(bot.close())
            await cancelling.wait()
            first.cancel()
            second = asyncio.create_task(bot.close())
            checkpoint = asyncio.Event()
            asyncio.get_running_loop().call_soon(checkpoint.set)
            await checkpoint.wait()
            first.cancel()
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
            await bot.close()
        loader.close.assert_awaited_once()
        discord_close.assert_awaited_once()
        autosave = bot.autosave_task.get_task()
        self.assertIsNotNone(autosave)
        if autosave is not None:
            self.assertTrue(autosave.done())

    async def test_watcher_failure_does_not_skip_discord_shutdown(self) -> None:
        loader = MagicMock(spec=CogLoader)
        loader.close.side_effect = RuntimeError("watcher failed")
        bot = StupidBot(cog_loader=loader)
        with (
            patch.object(commands.Bot, "close", new=AsyncMock()) as discord_close,
            self.assertLogs("framework.bot", level="ERROR"),
        ):
            await bot.close()
        discord_close.assert_awaited_once()


class TestCogLoaderShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_before_watcher_start_prevents_later_start(self) -> None:
        loader = CogLoader(MagicMock(), watch=True)
        await loader.close()
        with patch.object(loader, "_cog_watcher", new=AsyncMock()) as watcher:
            loader.start_watcher()
        watcher.assert_not_called()

    async def test_duplicate_start_and_cancelled_close_join_one_watcher(self) -> None:
        entered = asyncio.Event()
        stopping = asyncio.Event()
        release = asyncio.Event()
        stopped = asyncio.Event()

        async def watch() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopping.set()
                await release.wait()
                stopped.set()

        loader = CogLoader(MagicMock(), watch=True)
        with patch.object(
            loader, "_cog_watcher", new=AsyncMock(side_effect=watch)
        ) as watcher:
            loader.start_watcher()
            loader.start_watcher()
            await entered.wait()
            first = asyncio.create_task(loader.close())
            await stopping.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertFalse(stopped.is_set())
            release.set()
            await loader.close()
            await loader.close()
        self.assertTrue(stopped.is_set())
        watcher.assert_awaited_once()
