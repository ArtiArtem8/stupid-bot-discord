"""Tests for session restoration after a voice connection heal."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from api.music.healer import SessionHealer
from api.music.models import PlayerStateSnapshot, RepeatMode
from api.music.service.state_manager import StateManager


class TestSessionHealer(unittest.IsolatedAsyncioTestCase):
    async def test_restore_session_recreates_controller_for_current_track(self) -> None:
        bot = MagicMock()
        connection = MagicMock()
        state = StateManager()
        ui = MagicMock()
        ui.spawn_controller = AsyncMock()
        healer = SessionHealer(bot, connection, state, MagicMock(), ui)

        channel = MagicMock()
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

        await healer._restore_session(snapshot)

        player.play.assert_awaited_once()
        ui.spawn_controller.assert_awaited_once_with(player, track)
