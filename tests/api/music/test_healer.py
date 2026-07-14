"""Tests for session restoration after a voice connection heal."""

import asyncio
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import mafic
from discord import VoiceChannel

from api.music.healer import SessionHealer
from api.music.models import (
    ControllerDestroyReason,
    PlaybackAttempt,
    PlayerStateSnapshot,
    RepeatMode,
    VoiceCheckResult,
)
from api.music.player import MusicPlayer
from api.music.queue import QueueManager, RepeatManager
from api.music.service.state_manager import StateManager
from tests.api.music.helpers import make_entry


def _snapshot() -> PlayerStateSnapshot:
    return PlayerStateSnapshot(
        guild_id=1,
        voice_channel_id=2,
        text_channel_id=3,
        current_entry=None,
        position=100,
        is_paused=False,
        volume=50,
        queue=[],
        repeat_mode=RepeatMode.OFF,
        filters=None,
        session=None,
    )


class TestSessionHealer(unittest.IsolatedAsyncioTestCase):
    async def test_restore_session_returns_false_when_guild_is_missing(self) -> None:
        bot = MagicMock()
        bot.get_guild.return_value = None
        connection = MagicMock()
        healer = SessionHealer(
            bot, connection, StateManager(), MagicMock(), MagicMock()
        )

        restored = await healer._restore_session(_snapshot())

        self.assertFalse(restored)
        connection.join.assert_not_called()

    async def test_restore_session_returns_false_when_join_fails(self) -> None:
        bot = MagicMock()
        guild = MagicMock()
        guild.get_channel.return_value = MagicMock(spec=VoiceChannel)
        bot.get_guild.return_value = guild
        connection = MagicMock()
        connection.join = AsyncMock(
            return_value=(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None)
        )
        healer = SessionHealer(
            bot, connection, StateManager(), MagicMock(), MagicMock()
        )

        restored = await healer._restore_session(_snapshot())

        self.assertFalse(restored)
        connection.get_player.assert_not_called()

    async def test_restore_session_returns_false_when_player_is_missing(self) -> None:
        bot = MagicMock()
        guild = MagicMock()
        guild.get_channel.return_value = MagicMock(spec=VoiceChannel)
        bot.get_guild.return_value = guild
        connection = MagicMock()
        connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        connection.get_player.return_value = None
        healer = SessionHealer(
            bot, connection, StateManager(), MagicMock(), MagicMock()
        )

        restored = await healer._restore_session(_snapshot())

        self.assertFalse(restored)

    async def test_restore_session_recreates_controller_for_current_track(self) -> None:
        bot = MagicMock()
        connection = MagicMock()
        state = StateManager()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        healer = SessionHealer(bot, connection, state, MagicMock(), ui)

        channel = MagicMock(spec=VoiceChannel)

        connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        connection.is_player_usable = MagicMock(return_value=True)
        channel.connect = AsyncMock()
        guild = MagicMock()
        guild.get_channel.return_value = channel
        bot.get_guild.return_value = guild

        entry = make_entry("restored", entry_id=8, requester_id=42)
        player = MagicMock()
        player.restore_entries = MagicMock()
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[entry.track])
        player.restore_playback = AsyncMock()
        restored_attempt = PlaybackAttempt(1, entry)
        player.current_attempt = restored_attempt
        player.current = entry.track
        player.clear_current_attempt = AsyncMock()
        connection.get_player.return_value = player

        snapshot = PlayerStateSnapshot(
            guild_id=1,
            voice_channel_id=2,
            text_channel_id=3,
            current_entry=entry,
            position=100,
            is_paused=False,
            volume=50,
            queue=[],
            repeat_mode=RepeatMode.OFF,
            filters=None,
            session=None,
        )

        restored = await healer._restore_session(snapshot)
        self.assertTrue(restored)

        player.restore_entries.assert_called_once_with(entry, [])
        player.restore_playback.assert_awaited_once_with(
            entry, start_time=100, volume=50, pause=False
        )
        ui.spawn_controller.assert_awaited_once_with(player, restored_attempt)

    async def test_paused_restore_starts_paused_and_preserves_next_entry_id(
        self,
    ) -> None:
        bot = MagicMock()
        connection = MagicMock()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        healer = SessionHealer(bot, connection, StateManager(), MagicMock(), ui)
        guild = MagicMock()
        channel = MagicMock(spec=VoiceChannel)
        guild.get_channel.return_value = channel
        bot.get_guild.return_value = guild
        connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        current = make_entry("current", entry_id=8, requester_id=1)
        queued = make_entry("queued", entry_id=12, requester_id=2)
        player = MagicMock()
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[current.track])
        player.restore_playback = AsyncMock()
        player.current_attempt = PlaybackAttempt(1, current)
        player.current = current.track
        player.clear_current_attempt = AsyncMock()
        connection.get_player.return_value = player
        snapshot = PlayerStateSnapshot(
            guild_id=1,
            voice_channel_id=2,
            text_channel_id=3,
            current_entry=current,
            position=0,
            is_paused=True,
            volume=50,
            queue=[queued],
            repeat_mode=RepeatMode.OFF,
            filters=None,
            session=None,
        )

        self.assertTrue(await healer._restore_session(snapshot))

        player.restore_entries.assert_called_once_with(current, [queued])
        player.restore_playback.assert_awaited_once_with(
            current, start_time=0, volume=50, pause=True
        )

    async def test_immediate_load_failure_clears_attempt_and_fails_restore(
        self,
    ) -> None:
        bot = MagicMock()
        connection = MagicMock()
        state = StateManager()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        ui.controller.destroy_for_guild = AsyncMock()
        healer = SessionHealer(bot, connection, state, MagicMock(), ui)
        guild = MagicMock()
        guild.id = 1
        channel = MagicMock(spec=VoiceChannel)
        guild.get_channel.return_value = channel
        bot.get_guild.return_value = guild
        connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        entry = make_entry("failed", requester_id=42)
        attempt = PlaybackAttempt(1, entry)
        player = MusicPlayer.__new__(MusicPlayer)
        player.queue = QueueManager()
        player.repeat = RepeatManager()
        player._next_entry_id = 1
        player._next_attempt_id = 2
        player._current_attempt = attempt
        player._pending_end_attempts = deque()
        player._exception_attempt_ids = set()
        player._transition_lock = asyncio.Lock()
        player._current = None
        player.guild = guild
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[entry.track])
        player.restore_playback = AsyncMock()
        connection.get_player.return_value = player
        snapshot = PlayerStateSnapshot(
            guild_id=1,
            voice_channel_id=2,
            text_channel_id=3,
            current_entry=entry,
            position=0,
            is_paused=False,
            volume=50,
            queue=[],
            repeat_mode=RepeatMode.OFF,
            filters=None,
            session=None,
        )

        restored = await healer._restore_session(snapshot)

        self.assertFalse(restored)
        self.assertIsNone(player.current_attempt)
        connection.detach_stale_voice_client.assert_awaited_once_with(guild, player)
        ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.TRACK_EXCEPTION
        )
        ui.spawn_controller.assert_not_awaited()

    async def test_mismatched_mafic_current_detaches_restoring_player(self) -> None:
        connection = MagicMock()
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        ui.controller.destroy_for_guild = AsyncMock()
        healer = SessionHealer(MagicMock(), connection, StateManager(), MagicMock(), ui)
        guild = MagicMock()
        attempt = PlaybackAttempt(1, make_entry("expected"))
        player = MagicMock(
            guild=guild,
            current_attempt=attempt,
            current=make_entry("other").track,
        )
        player.clear_current_attempt = AsyncMock(return_value=True)

        restored = await healer._confirm_restored_track_active(
            player, guild_id=1, context="test"
        )

        self.assertFalse(restored)
        player.clear_current_attempt.assert_awaited_once_with(attempt)
        connection.detach_stale_voice_client.assert_awaited_once_with(guild, player)
        ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.TRACK_EXCEPTION
        )
        ui.spawn_controller.assert_not_awaited()

    async def test_restore_replaces_old_start_time_with_new_attempt_history(
        self,
    ) -> None:
        bot = MagicMock()
        connection = MagicMock()
        state = StateManager()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        healer = SessionHealer(bot, connection, state, MagicMock(), ui)
        old = PlaybackAttempt(7, make_entry("old", requester_id=99))
        state.record_track_start(1, old)
        guild = MagicMock()
        channel = MagicMock(spec=VoiceChannel)
        guild.get_channel.return_value = channel
        bot.get_guild.return_value = guild
        connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        connection.is_player_usable.return_value = True
        entry = make_entry("restored", requester_id=42)
        attempt = PlaybackAttempt(1, entry)
        player = MagicMock(current_attempt=attempt, current=entry.track)
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[entry.track])
        player.restore_playback = AsyncMock()
        player.clear_current_attempt = AsyncMock()
        connection.get_player.return_value = player
        snapshot = PlayerStateSnapshot(
            guild_id=1,
            voice_channel_id=2,
            text_channel_id=3,
            current_entry=entry,
            position=0,
            is_paused=False,
            volume=50,
            queue=[],
            repeat_mode=RepeatMode.OFF,
            filters=None,
            session=None,
        )

        self.assertTrue(await healer._restore_session(snapshot))
        self.assertNotIn((1, old.attempt_id), state._track_start_times_dt)
        self.assertIn((1, attempt.attempt_id), state._track_start_times_dt)

        state.record_history(1, attempt, mafic.EndReason.FINISHED)

        session = state.get_session(1)
        if session is None:
            self.fail("expected restored session")
        self.assertEqual(session.tracks[-1].requester_id, 42)
