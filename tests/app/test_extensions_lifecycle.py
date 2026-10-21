"""Tests for cog-owned background work and resource shutdown."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.birthdays.birthday_cog import BirthdayCog
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.member_roles.member_roles_cog import ServerMonitorCog
from stupidbot.features.member_roles.restoration import ServerMonitoringManager
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.voice.discord.profile_cog import VoiceProfileCog
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


class TestCogShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_drains_wolfram_request_before_session_close(
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
                await self._assert_shutdown_drains(bot, entered, release, finished)
                self.assertTrue(request.cancelled())
                self.assertEqual(cog._requests, set())
                self.assertTrue(session.closed)
                self.assertIsNone(
                    bot.tree.get_command(cog.ctx_menu.name, type=cog.ctx_menu.type)
                )
            finally:
                release.set()
                request.cancel()
                await asyncio.wait_for(
                    asyncio.gather(request, return_exceptions=True), 5
                )

    async def test_shutdown_waits_for_profile_cleanup(self) -> None:
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
            await self._assert_shutdown_drains(bot, entered, release, finished)
            cleanup.assert_awaited_once()
        self.assertTrue(cog._media_renderer._closed)

    async def test_shutdown_waits_for_music_cleanup(self) -> None:
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
            await self._assert_shutdown_drains(bot, entered, release, finished)
            cleanup.assert_awaited_once()
        self.assertTrue(cog.components.connection._closing)
        self.assertTrue(cog.components.controllers._closing)

    async def _assert_shutdown_drains(
        self,
        bot: StupidBot,
        entered: asyncio.Event,
        release: asyncio.Event,
        finished: asyncio.Event,
    ) -> None:
        closing = asyncio.create_task(bot.close())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertFalse(closing.done())
            self.assertFalse(finished.is_set())
            release.set()
            await asyncio.wait_for(asyncio.shield(closing), 5)
            self.assertTrue(finished.is_set())
            self.assertTrue(bot.is_closed())
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(closing), 5)
