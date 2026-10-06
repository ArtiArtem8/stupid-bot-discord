"""Bot-owned background work completes before Discord shutdown and final save."""

import asyncio
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

import config
from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from tests.storage import temporary_database


class TestBotShutdown(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.database_path, _ = await temporary_database(self)

    async def test_successful_startup_still_starts_and_closes_background_loops(
        self,
    ) -> None:
        ready = asyncio.Event()
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader, database_path=self.database_path)
        with (
            patch.object(bot.tree, "sync", new=AsyncMock()) as sync,
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=ready.wait)
            ),
        ):
            try:
                await bot.setup_hook()
                for loop in (bot.autosave_task, bot.update_activity_task):
                    self.assertIsNotNone(loop.get_task())
                    self.assertTrue(loop.is_running())
                loader.start_watcher.assert_called_once()
            finally:
                await bot.close()
        loader.load_cogs.assert_awaited_once()
        sync.assert_awaited_once()
        loader.close.assert_awaited_once()
        self.assertTrue(bot.is_closed())
        for loop in (bot.autosave_task, bot.update_activity_task):
            task = loop.get_task()
            self.assertIsNotNone(task)
            if task is not None:
                self.assertTrue(task.done())

    async def test_close_before_startup_is_idempotent(self) -> None:
        bot = StupidBot(database_path=self.database_path)
        await bot.close()
        await bot.close()
        self.assertTrue(bot.is_closed())
        self.assertIsNone(bot.autosave_task.get_task())
        self.assertIsNone(bot.update_activity_task.get_task())

    async def test_partial_startup_failure_still_closes_loaded_resources(self) -> None:
        loader = MagicMock(spec=CogLoader)
        loader.load_cogs.side_effect = RuntimeError("partial load")
        bot = StupidBot(cog_loader=loader, database_path=self.database_path)
        with patch.object(commands.Bot, "close", new=AsyncMock()) as discord_close:
            with self.assertRaises(RuntimeError):
                await bot.setup_hook()
            await bot.close()
        loader.close.assert_awaited_once()
        discord_close.assert_awaited_once()

    async def test_close_during_load_or_sync_prevents_background_start(self) -> None:
        async def pause(entered: asyncio.Event, release: asyncio.Event) -> None:
            entered.set()
            await release.wait()

        for stage in ("load", "sync"):
            with self.subTest(stage=stage):
                entered = asyncio.Event()
                release = asyncio.Event()
                ready = asyncio.Event()
                loader = MagicMock(spec=CogLoader)
                bot = StupidBot(cog_loader=loader, database_path=self.database_path)

                with (
                    patch.object(bot.tree, "sync", new=AsyncMock()) as sync,
                    patch.object(
                        bot, "wait_until_ready", new=AsyncMock(side_effect=ready.wait)
                    ),
                ):
                    boundary = loader.load_cogs if stage == "load" else sync
                    boundary.side_effect = partial(pause, entered, release)
                    startup = asyncio.create_task(bot.setup_hook())
                    try:
                        await entered.wait()
                        await bot.close()
                        release.set()
                        await asyncio.gather(startup, return_exceptions=True)
                        self.assertTrue(bot.is_closed())
                        self.assertIsNone(bot.autosave_task.get_task())
                        self.assertIsNone(bot.update_activity_task.get_task())
                        loader.start_watcher.assert_not_called()
                        if stage == "load":
                            sync.assert_not_awaited()
                        await bot.close()
                    finally:
                        release.set()
                        await asyncio.gather(startup, return_exceptions=True)
                        for loop in (bot.autosave_task, bot.update_activity_task):
                            loop.cancel()
                            if task := loop.get_task():
                                await asyncio.gather(task, return_exceptions=True)

    async def test_close_waits_for_startup_cleanup_even_if_cancellation_is_caught(
        self,
    ) -> None:
        class StartupCog(commands.Cog):
            def __init__(self, unloaded: asyncio.Event) -> None:
                self.unloaded = unloaded

            @override
            async def cog_unload(self) -> None:
                self.unloaded.set()

        async def pause(
            entered: asyncio.Event, cancelling: asyncio.Event, release: asyncio.Event
        ) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()

        for stage in ("load", "sync"):
            with self.subTest(stage=stage):
                entered = asyncio.Event()
                cancelling = asyncio.Event()
                release = asyncio.Event()
                unloaded = asyncio.Event()
                loader = MagicMock(spec=CogLoader)
                bot = StupidBot(cog_loader=loader, database_path=self.database_path)

                await bot.add_cog(StartupCog(unloaded))

                with patch.object(bot.tree, "sync", new=AsyncMock()) as sync:
                    boundary = loader.load_cogs if stage == "load" else sync
                    boundary.side_effect = partial(pause, entered, cancelling, release)
                    startup = asyncio.create_task(bot.setup_hook())
                    closing: asyncio.Task[None] | None = None
                    try:
                        await entered.wait()
                        closing = asyncio.create_task(bot.close())
                        await asyncio.wait_for(cancelling.wait(), 5)
                        self.assertFalse(closing.done())
                        self.assertFalse(unloaded.is_set())
                    finally:
                        release.set()
                        await asyncio.gather(startup, return_exceptions=True)
                        if closing is not None:
                            await closing
                    self.assertTrue(unloaded.is_set())
                    self.assertEqual(bot.cogs, {})
                    self.assertTrue(bot.is_closed())
                    self.assertIsNone(bot.autosave_task.get_task())
                    self.assertIsNone(bot.update_activity_task.get_task())
                    loader.start_watcher.assert_not_called()
                    if stage == "load":
                        sync.assert_not_awaited()

    async def test_setup_after_close_does_not_load_or_sync(self) -> None:
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader, database_path=self.database_path)
        await bot.close()
        ready = asyncio.Event()
        with (
            patch.object(bot.tree, "sync", new=AsyncMock()) as sync,
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=ready.wait)
            ),
        ):
            try:
                await bot.setup_hook()
                loader.load_cogs.assert_not_awaited()
                sync.assert_not_awaited()
                loader.start_watcher.assert_not_called()
            finally:
                for loop in (bot.autosave_task, bot.update_activity_task):
                    loop.cancel()
                    if task := loop.get_task():
                        await asyncio.gather(task, return_exceptions=True)

    async def test_cancelled_and_concurrent_close_wait_for_inflight_autosave(
        self,
    ) -> None:
        started = asyncio.Event()
        cancelling = asyncio.Event()
        release = asyncio.Event()
        persisted = asyncio.Event()
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader, database_path=self.database_path)

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
        bot = StupidBot(cog_loader=loader, database_path=self.database_path)
        with (
            patch.object(commands.Bot, "close", new=AsyncMock()) as discord_close,
            self.assertLogs("framework.bot", level="ERROR"),
        ):
            await bot.close()
        discord_close.assert_awaited_once()


class TestCogLoaderShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_during_load_prevents_loading_remaining_extensions(
        self,
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def load(_name: str) -> None:
            entered.set()
            await release.wait()

        bot = MagicMock(spec=commands.Bot)
        bot.load_extension.side_effect = load
        loader = CogLoader(bot)
        with TemporaryDirectory() as root:
            directory = Path(root)
            for name in ("first_cog.py", "second_cog.py"):
                (directory / name).touch()
            with (
                patch.object(config, "BASE_DIR", directory),
                patch.object(config, "COGS_DIR", directory),
            ):
                loading = asyncio.create_task(loader.load_cogs())
                try:
                    await entered.wait()
                    await loader.close()
                finally:
                    release.set()
                    await loading
                await loader.load_cogs()
        bot.load_extension.assert_awaited_once()

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
