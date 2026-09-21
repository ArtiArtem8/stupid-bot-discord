import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import MagicMock, patch

import discord
from discord.ext import commands

import config
from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceJournalRecord,
    VoiceObservation,
    VoiceSnapshot,
)
from api.voice.timeline import build_timeline
from cogs.voice.collector_cog import VoiceCollectorCog, normalize_voice_state, setup
from repositories.voice_journal import VoiceJournal
from tests.api.voice.examples import START, at
from utils.json_types import JsonObject


class Clock:
    seconds: float = 0

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return at(self.seconds)


def gateway_state(channel: str | None = "10", *, bot: bool = False) -> JsonObject:
    return {
        "guild_id": "1",
        "user_id": "123",
        "channel_id": channel,
        "member": {"user": {"id": "123", "bot": bot}},
        "self_mute": False,
        "self_deaf": False,
        "mute": False,
        "deaf": False,
        "self_stream": False,
        "self_video": False,
        "suppress": False,
        "request_to_speak_timestamp": at(0).isoformat(),
        "session_id": "voice-session",
    }


class TestNormalization(unittest.TestCase):
    def test_unresolved_raw_ids_and_timestamp_survive_without_discord_objects(
        self,
    ) -> None:
        payload = gateway_state()
        payload.pop("member")
        state = normalize_voice_state(payload)
        self.assertEqual((state.user_id, state.channel_id), (123, 10))
        self.assertIsNone(state.is_bot)
        self.assertEqual(state.requested_to_speak_at, at(0))
        self.assertEqual(state.session_id, "voice-session")

    def test_human_and_bot_use_identical_flags_model(self) -> None:
        for is_bot in (False, True):
            state = normalize_voice_state(gateway_state(bot=is_bot))
            self.assertIs(state.is_bot, is_bot)
            self.assertIs(state.self_mute, False)
            self.assertIs(state.self_video, False)


class TestCollector(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        enabled = patch.object(config, "VOICE_PROBE_ENABLED", True)
        enabled.start()
        self.addCleanup(enabled.stop)
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        await self.bot.__aenter__()
        self.journal = VoiceJournal(Path(self.directory.name))
        self.clock = Clock()
        self.cog = VoiceCollectorCog(
            self.bot,
            journal=self.journal,
            now=self.clock.now,
            monotonic=self.clock.monotonic,
        )
        await self.cog.cog_load()

    @override
    async def asyncTearDown(self) -> None:
        await self.cog.cog_unload()
        await self.bot.close()

    async def records(self) -> tuple[VoiceJournalRecord, ...]:
        await self.cog.cog_unload()
        return (
            *await self.journal.read_day(1, START.date()),
            *await self.journal.read_day(None, START.date()),
        )

    async def send_state(self, payload: JsonObject) -> None:
        await self.cog.on_socket_raw_receive(
            json.dumps({"t": "VOICE_STATE_UPDATE", "d": payload})
        )

    async def test_startup_snapshot_credits_existing_user(self) -> None:
        await self.cog.on_socket_raw_receive(
            json.dumps(
                {
                    "t": "GUILD_CREATE",
                    "d": {"id": "1", "voice_states": [gateway_state()]},
                }
            )
        )
        self.clock.seconds = 60
        await self.cog.on_ready()
        timeline = build_timeline(await self.records())
        self.assertEqual(presence(timeline, 123).total_seconds, 60)

    async def test_join_move_flags_leave_are_raw_observations(self) -> None:
        payload = gateway_state()
        await self.send_state(payload)
        self.clock.seconds = 10
        payload["channel_id"] = "20"
        await self.send_state(payload)
        self.clock.seconds = 20
        payload.update(
            self_mute=True, self_deaf=True, self_stream=True, self_video=True
        )
        await self.send_state(payload)
        self.clock.seconds = 30
        payload["channel_id"] = None
        await self.send_state(payload)
        observations = [
            r.fact.state
            for r in await self.records()
            if isinstance(r.fact, VoiceObservation)
        ]
        self.assertEqual([s.channel_id for s in observations], [10, 20, 20, None])
        self.assertTrue(observations[2].self_stream)
        self.assertTrue(observations[2].self_video)
        self.assertTrue(observations[2].self_deaf)
        self.assertTrue(observations[2].self_mute)
        self.assertEqual(observations[2].session_id, "voice-session")

    async def test_cache_snapshot_keeps_unknown_member_and_full_state(self) -> None:
        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.get_member.return_value = None
        guild.afk_channel = None
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = 10
        state = MagicMock(spec=discord.VoiceState)
        state.channel = channel
        state.self_mute = state.self_deaf = state.self_stream = state.self_video = True
        state.mute = state.deaf = state.suppress = False
        state.requested_to_speak_at = at(5)
        state.session_id = "known-session"
        guild._voice_states = {123: state}
        self.cog._snapshot_guild(guild)
        snapshots = [
            r.fact for r in await self.records() if isinstance(r.fact, VoiceSnapshot)
        ]
        self.assertTrue(snapshots[0].authoritative)
        observed = snapshots[0].states[0]
        self.assertEqual(observed.user_id, 123)
        self.assertIsNone(observed.is_bot)
        self.assertEqual(observed.requested_to_speak_at, at(5))
        self.assertEqual(observed.session_id, "known-session")
        self.assertTrue(observed.self_mute)

    async def test_unresolved_cache_channel_is_explicitly_non_authoritative(
        self,
    ) -> None:
        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.get_member.return_value = None
        state = MagicMock(spec=discord.VoiceState)
        state.channel = None
        state.self_mute = state.self_deaf = state.self_stream = state.self_video = False
        state.mute = state.deaf = state.suppress = False
        state.requested_to_speak_at = state.session_id = None
        guild._voice_states = {123: state}
        self.cog._snapshot_guild(guild)
        snapshots = [
            r.fact for r in await self.records() if isinstance(r.fact, VoiceSnapshot)
        ]
        self.assertFalse(snapshots[0].authoritative)
        self.assertFalse(snapshots[0].states[0].channel_known)
        self.assertEqual(snapshots[0].states[0].user_id, 123)

    async def test_offline_heartbeat_does_not_snapshot_or_confirm_cache(self) -> None:
        await self.cog.on_ready()
        self.clock.seconds = 10
        await self.cog.on_disconnect()
        self.clock.seconds = 60
        self.cog._heartbeat_once()
        records = await self.records()
        self.assertTrue(
            any(
                isinstance(r.fact, ObservationGap)
                and r.fact.reason == GapReason.GATEWAY_DISCONNECT
                for r in records
            )
        )
        self.assertFalse(
            any(
                isinstance(r.fact, (VoiceSnapshot, VoiceCheckpoint))
                and r.observed_at > at(10)
                for r in records
            )
        )

    async def test_clock_jump_writes_explicit_gap(self) -> None:
        await self.cog.on_ready()
        self.clock.seconds = 600
        self.cog._heartbeat_once()
        gaps = [
            r.fact for r in await self.records() if isinstance(r.fact, ObservationGap)
        ]
        self.assertEqual(gaps[0].reason, GapReason.CLOCK_DISCONTINUITY)
        self.assertEqual(gaps[0].started_at, at(0))

    async def test_unrelated_gateway_data_is_never_journaled(self) -> None:
        before = self.journal.counts.received
        await self.cog.on_socket_raw_receive(
            json.dumps({"t": "MESSAGE_CREATE", "d": {"content": "private"}})
        )
        self.assertEqual(self.journal.counts.received, before)

    async def test_setup_does_not_register_duplicate_collectors(self) -> None:
        with (
            patch.object(self.bot, "get_cog", return_value=self.cog),
            patch.object(self.bot, "add_cog") as add,
        ):
            await setup(self.bot)
            add.assert_not_called()

    async def test_disabled_collector_preserves_existing_debug_setting(self) -> None:
        await self.cog.cog_unload()
        journal = VoiceJournal(Path(self.directory.name))
        disabled = VoiceCollectorCog(self.bot, journal=journal)
        with (
            patch.object(config, "VOICE_PROBE_ENABLED", False),
            patch.object(self.bot, "_enable_debug_events", True),
        ):
            await disabled.cog_load()
            await disabled.cog_unload()
            self.assertTrue(self.bot._enable_debug_events)
        self.assertEqual(journal.counts.received, 0)
