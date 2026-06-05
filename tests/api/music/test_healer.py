"""Tests for session restoration after a voice connection heal."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from discord import VoiceChannel

from api.music.healer import SessionHealer
from api.music.models import PlayerStateSnapshot, RepeatMode, VoiceCheckResult
from api.music.service.state_manager import StateManager


def _snapshot() -> PlayerStateSnapshot:
    return PlayerStateSnapshot(
        guild_id=1,
        voice_channel_id=2,
        text_channel_id=3,
        current_track=None,
        position=100,
        is_paused=False,
        volume=50,
        queue=[],
        repeat_mode=RepeatMode.OFF,
        filters=None,
        requester_map={},
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

        track = MagicMock()
        player = MagicMock()
        player.queue._queue.extend = MagicMock()
        player.set_volume = AsyncMock()
        player.play = AsyncMock()
        connection.get_player.return_value = player

        snapshot = PlayerStateSnapshot(
            guild_id=1,
            voice_channel_id=2,
            text_channel_id=3,
            current_track=track,
            position=100,
            is_paused=False,
            volume=50,
            queue=[],
            repeat_mode=RepeatMode.OFF,
            filters=None,
            requester_map={},
            session=None,
        )

        restored = await healer._restore_session(snapshot)
        self.assertTrue(restored)

        player.play.assert_awaited_once()
        ui.spawn_controller.assert_awaited_once_with(player, track)
