"""Diagnostics remain available when storage or optional services are unavailable."""

import asyncio
import logging
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from stupidbot import settings as config
from stupidbot.app.bot import StupidBot
from stupidbot.app.cog_loader import CogLoader
from stupidbot.app.health import HealthSnapshot, TaskHealth
from stupidbot.app.owner_cog import OwnerCog
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.features.voice.journal import JournalCounts
from stupidbot.storage.database import DatabaseHealth
from tests.storage.fixtures import temporary_database


def healthy_snapshot() -> HealthSnapshot:
    return HealthSnapshot(
        ready=True,
        latency_ms=12,
        uptime_seconds=60,
        checkpoint_us=1_761_000_000_000_000,
        database=DatabaseHealth(0, False, 0, 0),
        tasks=(TaskHealth("autosave_task", True),),
        voice_enabled=True,
        voice_writer=True,
        voice_pending=0,
        voice_counts=JournalCounts(2, 2, 2, 0, 0),
        voice_last_commit_at=None,
        analytics="ready",
        music_ready=True,
        renderer_ready=True,
    )


class TestHealthCommand(unittest.IsolatedAsyncioTestCase):
    def make_context(self) -> tuple[OwnerCog, MagicMock, MagicMock]:
        bot = MagicMock(spec=StupidBot)
        bot.health_snapshot.return_value = healthy_snapshot()
        bot.preferences_repository = MagicMock(spec=PreferencesRepository)
        bot.preferences_repository.get.return_value = Preferences()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock(spec=discord.InteractionResponse)
        interaction.response.is_done.return_value = False
        interaction.extras = {}
        interaction.locale = discord.Locale.american_english
        interaction.client = bot
        interaction.followup.send = AsyncMock()
        interaction.guild = None
        interaction.user.id = 42
        return OwnerCog(bot), interaction, bot

    async def test_non_owner_cannot_run_health(self) -> None:
        cog, interaction, bot = self.make_context()
        bot.is_owner.return_value = False
        self.assertFalse(await cog.health._check_can_run(interaction))
        bot.health_snapshot.assert_not_called()
        interaction.followup.send.assert_not_awaited()

    async def test_health_acknowledges_before_preferences_and_survives_failure(
        self,
    ) -> None:
        cog, interaction, bot = self.make_context()

        async def fail_preferences(
            scope: PreferenceScope, subject_id: int
        ) -> Preferences:
            self.assertEqual(scope, PreferenceScope.USER)
            self.assertEqual(subject_id, 42)
            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True
            )
            raise RuntimeError("Database is closing")

        bot.preferences_repository.get.side_effect = fail_preferences
        await cog.health._do_call(interaction, {})
        bot.preferences_repository.get.assert_awaited_once()
        bot.health_snapshot.assert_called_once()
        call = interaction.followup.send.await_args
        if call is None:
            self.fail("Missing health response")
        self.assertTrue(call.kwargs["ephemeral"])
        embed = call.kwargs["embed"]
        self.assertIn("busy_total=0", embed.fields[0].value)
        self.assertLessEqual(len(embed.fields[0].value), 1024)

    async def test_slow_preferences_do_not_block_diagnostics(self) -> None:
        cog, interaction, bot = self.make_context()
        cancelled = asyncio.Event()

        async def wait_preferences(
            _scope: PreferenceScope, _subject_id: int
        ) -> Preferences:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

            return Preferences()

        bot.preferences_repository.get.side_effect = wait_preferences
        await asyncio.wait_for(cog.health._do_call(interaction, {}), timeout=2)
        self.assertTrue(cancelled.is_set())
        bot.preferences_repository.get.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()


class TestHealthSnapshot(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_requires_no_database_admission(self) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
        self.addAsyncCleanup(bot.close)
        await bot.restore_state()
        with patch.object(
            bot._database, "transaction", side_effect=AssertionError("Unexpected SQL")
        ):
            snapshot = bot.health_snapshot()
        self.assertEqual(snapshot.analytics, "ready")
        self.assertFalse(snapshot.music_ready)
        self.assertFalse(snapshot.renderer_ready)
        self.assertEqual(snapshot.database.admitted, 0)
        self.assertIsNotNone(snapshot.uptime_seconds)

    def test_disabled_voice_is_distinct_from_failed_writer(self) -> None:
        snapshot = replace(healthy_snapshot(), voice_enabled=False, voice_writer=False)
        self.assertFalse(snapshot.degraded)
        self.assertIn("voice_collection=disabled", snapshot.lines())
        enabled = replace(snapshot, voice_enabled=True)
        self.assertTrue(enabled.degraded)
        failed_task = replace(
            snapshot, tasks=(TaskHealth("autosave_task", False, failed=True),)
        )
        self.assertTrue(failed_task.degraded)
        self.assertIn("tasks=autosave_task:failed", failed_task.lines())

    def test_optional_service_unavailability_does_not_mark_core_failure(
        self,
    ) -> None:
        snapshot = replace(healthy_snapshot(), music_ready=False, renderer_ready=False)
        self.assertFalse(snapshot.degraded)
        self.assertIn(
            "analytics=ready lavalink_ready=False renderer_ready=False",
            snapshot.lines(),
        )

    async def test_periodic_log_reports_new_failures_without_latching_warning(
        self,
    ) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
        self.addAsyncCleanup(bot.close)
        first = healthy_snapshot()
        busy = replace(first, database=DatabaseHealth(0, False, 1, 0))
        with (
            patch.object(bot, "health_snapshot", side_effect=(first, busy, busy)),
            self.assertLogs("stupidbot.app.bot", level="INFO") as captured,
        ):
            for _ in range(3):
                await bot.health_task.coro(bot)
        self.assertEqual(
            [record.levelno for record in captured.records],
            [logging.INFO, logging.WARNING, logging.INFO],
        )
        self.assertTrue(
            all("\n" not in record.getMessage() for record in captured.records)
        )

    async def test_collection_setting_is_reflected_without_starting_a_writer(
        self,
    ) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
        self.addAsyncCleanup(bot.close)
        with patch.object(config, "VOICE_PROBE_ENABLED", False):
            snapshot = bot.health_snapshot()
        self.assertFalse(snapshot.voice_enabled)
        self.assertIsNone(snapshot.voice_counts)
