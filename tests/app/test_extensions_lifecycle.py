"""Regression tests for cog-owned background loops."""

import asyncio
import importlib
import sys
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.app import cog_loader
from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.birthdays.birthday_cog import BirthdayCog
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.member_roles.member_roles_cog import ServerMonitorCog
from stupidbot.features.member_roles.restoration import ServerMonitoringManager
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.voice.discord.profile_cog import VoiceProfileCog
from stupidbot.features.voice.profile.media import ProfileMediaRenderer
from stupidbot.features.wolfram.client import WolframClient, WolframResult
from stupidbot.features.wolfram.wolfram_cog import WolframCog
from tests.storage.fixtures import temporary_database


class TestCogLoopLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_birthday_unload_waits_for_timer_cleanup(self) -> None:
        entered = asyncio.Event()
        cancelling = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        manager = MagicMock(spec=BirthdayRepository)
        cog = BirthdayCog(bot, manager)

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
            manager, "get_all_guild_ids", new=AsyncMock(side_effect=guild_ids)
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
        cog = BirthdayCog(MagicMock(), MagicMock(spec=BirthdayRepository))
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
        cog = ServerMonitorCog(MagicMock(), MagicMock(spec=ServerMonitoringManager))
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


class TestRemovedCogShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_failed_cleanup_preserves_watcher_cancellation(self) -> None:
        for cog_type in (MusicCog, VoiceProfileCog, WolframCog):
            with self.subTest(cog=cog_type.__name__):
                await self._assert_failed_cleanup_stops_reload(cog_type)

    async def _assert_failed_cleanup_stops_reload(
        self, cog_type: type[MusicCog | VoiceProfileCog | WolframCog]
    ) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path)
        self.addAsyncCleanup(bot.close)
        nodes: dict[str, mafic.Node[commands.Bot]] = {}
        extension = f"reload_{cog_type.__name__.lower()}"
        arguments = "bot, bot.volume_repository" if cog_type is MusicCog else "bot"
        with (
            TemporaryDirectory() as directory,
            patch.object(sys, "path", [directory, *sys.path]),
            patch.object(mafic.NodePool, "_nodes", nodes),
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=asyncio.Event().wait)
            ),
            patch.object(
                ProfileMediaRenderer, "astart", new=AsyncMock(return_value="offline")
            ),
        ):
            # discord.py removes only cogs whose class belongs to the extension.
            Path(directory, f"{extension}.py").write_text(
                f"from {cog_type.__module__} import {cog_type.__name__}\n"
                f"class ReloadCog({cog_type.__name__}):\n    pass\n"
                "async def setup(bot):\n"
                f"    await bot.add_cog(ReloadCog({arguments}))\n",
                encoding="utf-8",
            )
            importlib.invalidate_caches()
            await bot.load_extension(extension)
            cog = bot.get_cog("ReloadCog")
            if isinstance(cog, MusicCog):
                owner = cog.components.controllers
                operation = "cleanup"
                close_resource = owner.cleanup
            elif isinstance(cog, VoiceProfileCog):
                owner = cog._media_renderer
                operation = "aclose"
                close_resource = owner.aclose
            elif isinstance(cog, WolframCog) and cog.client_session is not None:
                owner = cog.client_session
                operation = "close"
                close_resource = owner.close
            else:
                self.fail("Expected the loaded extension to own a cleanup resource")
            await self._assert_reload_cancelled_after_cleanup_failure(
                bot, owner, operation, close_resource
            )

    async def _assert_reload_cancelled_after_cleanup_failure(
        self,
        bot: StupidBot,
        owner: object,
        operation: str,
        close_resource: Callable[[], Awaitable[None]],
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        failure = BrokenPipeError("Resource cleanup failed during reload")

        async def failing_close() -> None:
            entered.set()
            await release.wait()
            await close_resource()
            raise failure

        stopping = asyncio.Event()
        stop_watcher = bot.cog_loader._stop_watcher

        async def observe_watcher_stop() -> None:
            stopping.set()
            await stop_watcher()

        closing: asyncio.Task[None] | None = None
        with (
            patch.object(owner, operation, new=AsyncMock(side_effect=failing_close)),
            patch.object(bot, "load_extension", wraps=bot.load_extension) as load,
            patch.object(bot.cog_loader, "_stop_watcher", new=observe_watcher_stop),
            patch.object(cog_loader, "time", new=MagicMock(time=lambda: 0)),
            self.assertLogs(level="ERROR") as logs,
        ):
            bot.cog_loader.enable_watch = True
            bot.cog_loader.start_watcher()
            watcher = bot.cog_loader._watcher_task
            if watcher is None:
                self.fail("Expected the real extension watcher to start")
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertNotIn("ReloadCog", bot.cogs)
                closing = asyncio.create_task(bot.close())
                await asyncio.wait_for(stopping.wait(), 2)
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                self.assertFalse(watcher.done())
                self.assertFalse(closing.done())
                release.set()
                await asyncio.wait_for(asyncio.shield(closing), 2)
                self.assertTrue(watcher.cancelled())
                load.assert_not_awaited()
                self.assertTrue(bot.is_closed())
                self.assertEqual(
                    [record.exc_info[1] for record in logs.records if record.exc_info],
                    [failure],
                )
                self.assertIn("ReloadCog", logs.records[0].getMessage())
            finally:
                release.set()
                watcher.cancel()
                tasks: list[asyncio.Task[object]] = [watcher]
                if closing is not None:
                    tasks.append(closing)
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), 5
                )

    async def test_shutdown_drains_removed_wolfram_request_before_session_close(
        self,
    ) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path)
        self.addAsyncCleanup(bot.close)
        cog = WolframCog(bot)
        started = asyncio.Event()
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def pending_request(_query: str) -> WolframResult:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                entered.set()
                await release.wait()
                finished.set()
            self.fail("The request must be cancelled during shutdown")

        interaction = MagicMock(spec=discord.Interaction)
        interaction.extras = {}
        interaction.guild_id = None
        interaction.locale = discord.Locale.american_english
        with (
            patch.object(config, "WOLFRAM_APP_ID", "offline"),
            patch.object(
                WolframClient, "query", new=AsyncMock(side_effect=pending_request)
            ),
            patch.object(FeedbackUI, "send", new=AsyncMock()),
        ):
            await bot.add_cog(cog)
            session = cog.client_session
            if session is None:
                self.fail("Expected the cog to own an HTTP session")
            self.addAsyncCleanup(session.close)
            request = asyncio.create_task(cog._handle_query(interaction, "x", "solve"))
            try:
                await asyncio.wait_for(started.wait(), 2)
                await self._assert_removed_cog_drains(
                    bot, cog, entered, release, finished
                )
                self.assertTrue(request.cancelled())
                self.assertEqual(cog._requests, set())
                self.assertTrue(session.closed)
                self.assertIsNone(
                    bot.tree.get_command(cog.ctx_menu.name, type=cog.ctx_menu.type)
                )
                await cog.cog_unload()
            finally:
                release.set()
                request.cancel()
                await asyncio.wait_for(
                    asyncio.gather(request, return_exceptions=True), 5
                )

    async def test_shutdown_waits_for_removed_profile_cleanup(self) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path)
        self.addAsyncCleanup(bot.close)
        cog = VoiceProfileCog(bot)
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        close_cache = cog._media_cache.aclose

        async def pending_cache_close() -> None:
            entered.set()
            await release.wait()
            await close_cache()
            finished.set()

        with (
            patch.object(
                cog._media_renderer, "astart", new=AsyncMock(return_value="offline")
            ),
            patch.object(
                cog._media_cache,
                "aclose",
                new=AsyncMock(side_effect=pending_cache_close),
            ) as cleanup,
        ):
            await bot.add_cog(cog)
            await self._assert_removed_cog_drains(bot, cog, entered, release, finished)
            await cog.cog_unload()
            cleanup.assert_awaited_once()
        self.assertTrue(cog._media_renderer._closed)

    async def test_shutdown_waits_for_removed_music_cleanup(self) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path)
        self.addAsyncCleanup(bot.close)
        cog = MusicCog(bot, bot.volume_repository)
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        close_playback = cog.components.playback_events.cleanup

        async def pending_playback_cleanup() -> None:
            entered.set()
            await release.wait()
            await close_playback()
            finished.set()

        nodes: dict[str, mafic.Node[commands.Bot]] = {}
        with (
            patch.object(mafic.NodePool, "_nodes", nodes),
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=asyncio.Event().wait)
            ),
            patch.object(
                cog.components.playback_events,
                "cleanup",
                new=AsyncMock(side_effect=pending_playback_cleanup),
            ) as cleanup,
        ):
            await bot.add_cog(cog)
            await self._assert_removed_cog_drains(bot, cog, entered, release, finished)
            await cog.cog_unload()
            cleanup.assert_awaited_once()
        self.assertTrue(cog.components.connection._closing)
        self.assertTrue(cog.components.controllers._closing)

    async def _assert_removed_cog_drains(
        self,
        bot: StupidBot,
        cog: commands.Cog,
        entered: asyncio.Event,
        release: asyncio.Event,
        finished: asyncio.Event,
    ) -> None:
        async def reload_removal() -> NoReturn:
            await bot.remove_cog(cog.qualified_name)
            self.fail("The watcher must propagate cancellation after cleanup")

        stopping = asyncio.Event()
        stop_watcher = bot.cog_loader._stop_watcher

        async def observe_watcher_stop() -> None:
            stopping.set()
            await stop_watcher()

        unloading = asyncio.create_task(reload_removal())
        bot.cog_loader._watcher_task = unloading
        closing: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertNotIn(cog.qualified_name, bot.cogs)
            with patch.object(
                bot.cog_loader, "_stop_watcher", new=observe_watcher_stop
            ):
                closing = asyncio.create_task(bot.close())
                await asyncio.wait_for(stopping.wait(), 2)
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                self.assertFalse(unloading.done())
                self.assertFalse(closing.done())
                self.assertFalse(finished.is_set())
                release.set()
                await asyncio.wait_for(closing, 5)
            with self.assertRaises(asyncio.CancelledError):
                await unloading
            self.assertTrue(finished.is_set())
            self.assertTrue(bot.is_closed())
        finally:
            release.set()
            tasks: list[asyncio.Task[object]] = [unloading]
            if closing is not None:
                tasks.append(closing)
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
