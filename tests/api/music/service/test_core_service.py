"""Tests for soft music service availability failures."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic

from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    MusicResultStatus,
    VoiceCheckResult,
)
from api.music.service.core_service import CoreMusicService


class TestCoreMusicServiceAvailability(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.ensure_available = AsyncMock(return_value=False)
        self.connection.start_lazy_connect = MagicMock()
        self.connection.cleanup = AsyncMock()
        self.connection.get_player = MagicMock(return_value=None)
        self.connection.is_known_unavailable = MagicMock(return_value=True)
        self.connection.is_player_usable = MagicMock(return_value=False)
        self.connection.get_player_node = MagicMock(return_value=None)
        self.connection.mark_node_unavailable = AsyncMock()
        self.connection.detach_stale_voice_client = AsyncMock()
        self.state = MagicMock()
        self.volume_repo = MagicMock()
        self.events = MagicMock()
        self.ui = MagicMock()
        self.service = CoreMusicService(
            self.bot,
            self.connection,
            self.state,
            self.volume_repo,
            self.events,
            self.ui,
        )

    async def test_initialize_does_not_raise_when_connection_unavailable(self) -> None:
        await self.service.initialize()

        self.events.setup.assert_called_once()
        self.assertTrue(self.service._initialized)
        self.connection.ensure_available.assert_not_awaited()
        self.connection.start_lazy_connect.assert_called_once()

    async def test_play_returns_unavailable_join_failure_without_player_lookup(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        channel = MagicMock()
        self.connection.join = AsyncMock(
            return_value=(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None)
        )

        result = await self.service.play(guild, channel, "query", 1, 2)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(
            result.data,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        self.connection.get_player.assert_not_called()

    async def test_no_player_command_returns_unavailable_when_known_down(self) -> None:
        result = await self.service.pause(123)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)

    async def test_leave_stale_voice_returns_unavailable_after_local_cleanup(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = object()
        self.connection.get_player.return_value = None
        self.connection.disconnect = AsyncMock()
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock()
        self.service.end_session = AsyncMock()  # type: ignore[method-assign]

        with patch("api.music.service.core_service.mafic.Player", object):
            result = await self.service.leave(guild)

        self.connection.disconnect.assert_awaited_once_with(guild, force=True)
        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)

    async def test_join_returns_unavailable_when_apply_volume_http_not_found(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        channel = MagicMock()
        player = MagicMock()
        player.guild = guild
        self.connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player
        self.volume_repo.get_volume = AsyncMock(return_value=80)
        self.service._apply_volume = AsyncMock(  # type: ignore[method-assign]
            side_effect=mafic.HTTPNotFound("Session not found")
        )

        result = await self.service.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None))
        self.connection.mark_node_unavailable.assert_awaited_once()
        self.connection.detach_stale_voice_client.assert_awaited_once_with(
            guild, player
        )

    async def test_join_returns_unavailable_when_apply_volume_client_error(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        channel = MagicMock()
        player = MagicMock()
        player.guild = guild
        self.connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player
        self.volume_repo.get_volume = AsyncMock(return_value=80)
        self.service._apply_volume = AsyncMock(  # type: ignore[method-assign]
            side_effect=aiohttp.ClientConnectionError("down")
        )

        result = await self.service.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None))
        self.connection.mark_node_unavailable.assert_awaited_once()
