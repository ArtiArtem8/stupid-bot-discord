"""Tests for session restoration after a voice connection heal."""

import asyncio
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import mafic
from discord import VoiceChannel

from api.music.healer import SessionHealer
from api.music.models import (
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


def _runtime_player(current: PlaybackAttempt) -> MusicPlayer:
    player = MusicPlayer.__new__(MusicPlayer)
    player.queue = QueueManager()
    player.repeat = RepeatManager()
    player._next_entry_id = current.entry.entry_id + 1
    player._next_attempt_id = current.attempt_id + 1
    player._current_attempt = current
    player._pending_end_attempts = deque()
    player._exception_attempt_ids = set()
    player._transition_lock = asyncio.Lock()
    player._is_stale = False
    player._current = current.entry.track
    player.guild = MagicMock(id=1)
    return player


class TestSessionHealer(unittest.IsolatedAsyncioTestCase):
    def _make_warm_restore_healer(self) -> tuple[SessionHealer, MagicMock, MagicMock]:
        connection = MagicMock()
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        ui.spawn_controller = AsyncMock()
        healer = SessionHealer(MagicMock(), connection, StateManager(), MagicMock(), ui)
        return healer, connection, ui

    async def test_exact_restore_attempt_confirmation_succeeds(self) -> None:
        connection = MagicMock()
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        healer = SessionHealer(MagicMock(), connection, StateManager(), MagicMock(), ui)
        attempt = PlaybackAttempt(1, make_entry("expected"))
        player = MagicMock(
            current_attempt=attempt,
            current=attempt.entry.track,
        )
        player.invalidate_if_current_attempt = AsyncMock()

        with patch("api.music.healer.asyncio.sleep", new=AsyncMock()):
            restored = await healer._confirm_restored_track_active(
                player, attempt, guild_id=1, context="test"
            )

        self.assertTrue(restored)
        player.invalidate_if_current_attempt.assert_not_awaited()
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_failed_restore_claim_preserves_replacement_waiting_for_lock(
        self,
    ) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        expected = PlaybackAttempt(1, make_entry("expected", entry_id=1))
        replacement = PlaybackAttempt(2, make_entry("replacement", entry_id=2))
        player = _runtime_player(expected)
        entered = asyncio.Event()
        original_invalidate = MusicPlayer.invalidate_if_current_attempt

        async def invalidate(attempt: PlaybackAttempt) -> bool:
            entered.set()
            return await original_invalidate(player, attempt)

        await player._transition_lock.acquire()
        with patch.object(
            player,
            "invalidate_if_current_attempt",
            new=AsyncMock(side_effect=invalidate),
        ):
            task = asyncio.create_task(
                healer._fail_expected_restore_attempt(
                    player,
                    expected,
                    guild_id=1,
                    context="test-race",
                )
            )
            await entered.wait()
            player._current_attempt = replacement
            player._transition_lock.release()
            restored = await task

        self.assertFalse(restored)
        self.assertIs(player.current_attempt, replacement)
        self.assertFalse(player.is_stale)
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_failed_restore_claim_marks_player_stale_before_detach(self) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        expected = PlaybackAttempt(1, make_entry("expected"))
        player = _runtime_player(expected)
        player._exception_attempt_ids.add(expected.attempt_id)

        async def detach(_guild: object, actual_player: MusicPlayer) -> None:
            self.assertIs(actual_player, player)
            self.assertTrue(player.is_stale)

        connection.detach_stale_voice_client = AsyncMock(side_effect=detach)

        restored = await healer._fail_expected_restore_attempt(
            player,
            expected,
            guild_id=1,
            context="test-claim",
        )

        self.assertFalse(restored)
        self.assertIsNone(player.current_attempt)
        self.assertTrue(player.is_stale)
        self.assertNotIn(expected.attempt_id, player._exception_attempt_ids)
        connection.detach_stale_voice_client.assert_awaited_once_with(
            player.guild, player
        )
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_superseded_restore_confirmation_preserves_new_attempt(self) -> None:
        connection = MagicMock()
        connection.is_player_usable.return_value = True
        connection.detach_stale_voice_client = AsyncMock()
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        healer = SessionHealer(MagicMock(), connection, StateManager(), MagicMock(), ui)
        expected = PlaybackAttempt(1, make_entry("same", entry_id=1))
        replacement = PlaybackAttempt(2, make_entry("same", entry_id=2))
        player = MagicMock(
            current_attempt=replacement,
            current=replacement.entry.track,
        )
        player.invalidate_if_current_attempt = AsyncMock(return_value=False)

        with patch("api.music.healer.asyncio.sleep", new=AsyncMock()):
            restored = await healer._confirm_restored_track_active(
                player, expected, guild_id=1, context="test"
            )

        self.assertFalse(restored)
        self.assertIs(player.current_attempt, replacement)
        player.invalidate_if_current_attempt.assert_awaited_once_with(expected)
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_old_player_confirmation_preserves_replacement_voice_client(
        self,
    ) -> None:
        connection = MagicMock()
        connection.is_player_usable.return_value = False
        replacement_player = MagicMock()
        guild = MagicMock(voice_client=replacement_player)
        expected = PlaybackAttempt(1, make_entry("expected"))
        old_player = MagicMock(
            guild=guild,
            current_attempt=expected,
            current=expected.entry.track,
        )
        old_player.invalidate_if_current_attempt = AsyncMock(return_value=True)

        async def detach_old(actual_guild: object, actual_player: object) -> None:
            self.assertIs(actual_guild, guild)
            self.assertIs(actual_player, old_player)
            self.assertIs(guild.voice_client, replacement_player)

        connection.detach_stale_voice_client = AsyncMock(side_effect=detach_old)
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        healer = SessionHealer(MagicMock(), connection, StateManager(), MagicMock(), ui)

        with patch("api.music.healer.asyncio.sleep", new=AsyncMock()):
            restored = await healer._confirm_restored_track_active(
                old_player, expected, guild_id=1, context="test"
            )

        self.assertFalse(restored)
        self.assertIs(guild.voice_client, replacement_player)
        old_player.invalidate_if_current_attempt.assert_awaited_once_with(expected)
        connection.detach_stale_voice_client.assert_awaited_once_with(guild, old_player)
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_successful_warm_seek_keeps_exact_attempt(self) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        guild = MagicMock(id=1)
        entry = make_entry("warm")
        attempt = PlaybackAttempt(1, entry)
        player = MagicMock(guild=guild, current_attempt=None, current=None)
        player.seek = AsyncMock()
        player.set_volume = AsyncMock()
        player.pause = AsyncMock()

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player.current_attempt = attempt
            player.current = entry.track
            return attempt

        player.restore_playback = AsyncMock(side_effect=restore_playback)
        player.invalidate_if_current_attempt = AsyncMock(return_value=False)

        with patch("api.music.healer.asyncio.sleep", new=AsyncMock()):
            restored = await healer._play_with_warm_seek_restore(
                guild=guild,
                player=player,
                entry=entry,
                position=4_000,
                volume=65,
                pause=False,
            )

        self.assertTrue(restored)
        player.seek.assert_awaited_once_with(4_000)
        player.set_volume.assert_awaited_once_with(65)
        player.pause.assert_not_awaited()
        self.assertIs(player.current_attempt, attempt)
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_warm_seek_error_restores_state_when_attempt_is_alive(self) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        guild = MagicMock(id=1)
        attempt = PlaybackAttempt(1, make_entry("warm"))
        player = MagicMock(
            guild=guild,
            current_attempt=attempt,
            current=attempt.entry.track,
        )
        player.seek = AsyncMock(side_effect=TimeoutError)
        player.set_volume = AsyncMock()
        player.pause = AsyncMock()
        player.invalidate_if_current_attempt = AsyncMock()

        restored = await healer._seek_after_warm_restore(
            guild=guild,
            player=player,
            entry=attempt.entry,
            position=4_000,
            volume=65,
            pause=True,
            expected_attempt=attempt,
        )

        self.assertTrue(restored)
        player.set_volume.assert_awaited_once_with(65)
        player.pause.assert_awaited_once_with()
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_warm_seek_killed_attempt_falls_back_without_pending(self) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        guild = MagicMock(id=1)
        entry = make_entry("warm")
        player = MusicPlayer.__new__(MusicPlayer)
        player.queue = QueueManager()
        player.repeat = RepeatManager()
        player._next_entry_id = 2
        player._next_attempt_id = 1
        player._current_attempt = None
        player._pending_end_attempts = deque()
        player._exception_attempt_ids = set()
        player._transition_lock = asyncio.Lock()
        player._current = None
        player.guild = guild

        async def play(track: mafic.Track, **_kwargs: object) -> None:
            player._current = track

        async def seek(_position: int) -> None:
            player._current = None

        with (
            patch.object(player, "play", new=AsyncMock(side_effect=play)),
            patch.object(player, "seek", new=AsyncMock(side_effect=seek)),
            patch.object(player, "set_volume", new=AsyncMock()),
            patch("api.music.healer.asyncio.sleep", new=AsyncMock()),
        ):
            restored = await healer._play_with_warm_seek_restore(
                guild=guild,
                player=player,
                entry=entry,
                position=4_000,
                volume=65,
                pause=False,
            )

        self.assertTrue(restored)
        self.assertIsNotNone(player.current_attempt)
        if player.current_attempt is None:
            self.fail("expected fallback attempt")
        self.assertEqual(player.current_attempt.attempt_id, 2)
        self.assertEqual(list(player._pending_end_attempts), [])
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_post_warm_seek_volume_failure_detaches_exact_attempt(self) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        guild = MagicMock(id=1)
        attempt = PlaybackAttempt(1, make_entry("warm"))
        player = MagicMock(
            guild=guild,
            current_attempt=attempt,
            current=attempt.entry.track,
        )
        player.seek = AsyncMock()
        player.set_volume = AsyncMock(side_effect=TimeoutError)

        async def invalidate_current(expected: PlaybackAttempt) -> bool:
            self.assertIs(expected, attempt)
            player.current_attempt = None
            return True

        player.invalidate_if_current_attempt = AsyncMock(side_effect=invalidate_current)

        with patch("api.music.healer.asyncio.sleep", new=AsyncMock()):
            restored = await healer._seek_after_warm_restore(
                guild=guild,
                player=player,
                entry=attempt.entry,
                position=4_000,
                volume=65,
                pause=False,
                expected_attempt=attempt,
            )

        self.assertFalse(restored)
        self.assertIsNone(player.current_attempt)
        connection.detach_stale_voice_client.assert_awaited_once_with(guild, player)
        ui.spawn_controller.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_warm_seek_superseded_during_delay_preserves_replacement(
        self,
    ) -> None:
        healer, connection, ui = self._make_warm_restore_healer()
        guild = MagicMock(id=1)
        entry = make_entry("same", entry_id=1)
        expected = PlaybackAttempt(1, entry)
        replacement = PlaybackAttempt(2, make_entry("same", entry_id=2))
        player = MagicMock(guild=guild, current_attempt=None, current=None)
        player.seek = AsyncMock()
        player.set_volume = AsyncMock()
        player.invalidate_if_current_attempt = AsyncMock(return_value=False)

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player.current_attempt = expected
            player.current = expected.entry.track
            return expected

        sleep_count = 0

        async def confirmation_delay(_delay: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count == 2:
                player.current_attempt = replacement
                player.current = replacement.entry.track

        player.restore_playback = AsyncMock(side_effect=restore_playback)

        with patch(
            "api.music.healer.asyncio.sleep",
            new=AsyncMock(side_effect=confirmation_delay),
        ):
            restored = await healer._play_with_warm_seek_restore(
                guild=guild,
                player=player,
                entry=entry,
                position=4_000,
                volume=65,
                pause=False,
            )

        self.assertFalse(restored)
        self.assertIs(player.current_attempt, replacement)
        player.invalidate_if_current_attempt.assert_awaited_once_with(expected)
        connection.detach_stale_voice_client.assert_not_awaited()
        ui.controller.destroy_for_guild.assert_not_awaited()

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
        restored_attempt = PlaybackAttempt(1, entry)

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player.current_attempt = restored_attempt
            player.current = entry.track
            return restored_attempt

        player.restore_playback = AsyncMock(side_effect=restore_playback)
        player.current_attempt = None
        player.current = None
        player.invalidate_if_current_attempt = AsyncMock()
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
        attempt = PlaybackAttempt(1, current)

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player.current_attempt = attempt
            player.current = current.track
            return attempt

        player.restore_playback = AsyncMock(side_effect=restore_playback)
        player.current_attempt = None
        player.current = None
        player.invalidate_if_current_attempt = AsyncMock()
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
        player._current_attempt = None
        player._pending_end_attempts = deque()
        player._exception_attempt_ids = set()
        player._transition_lock = asyncio.Lock()
        player._current = None
        player.guild = guild
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[entry.track])

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player._current_attempt = attempt
            player._current = None
            return attempt

        player.restore_playback = AsyncMock(side_effect=restore_playback)
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
        ui.controller.destroy_for_guild.assert_not_awaited()
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
        player.invalidate_if_current_attempt = AsyncMock(return_value=True)

        restored = await healer._confirm_restored_track_active(
            player, attempt, guild_id=1, context="test"
        )

        self.assertFalse(restored)
        player.invalidate_if_current_attempt.assert_awaited_once_with(attempt)
        connection.detach_stale_voice_client.assert_awaited_once_with(guild, player)
        ui.controller.destroy_for_guild.assert_not_awaited()
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
        player = MagicMock(current_attempt=None, current=None)
        player.set_volume = AsyncMock()
        player.fetch_tracks = AsyncMock(return_value=[entry.track])

        async def restore_playback(
            _entry: object, **_kwargs: object
        ) -> PlaybackAttempt:
            player.current_attempt = attempt
            player.current = entry.track
            return attempt

        player.restore_playback = AsyncMock(side_effect=restore_playback)
        player.invalidate_if_current_attempt = AsyncMock()
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
