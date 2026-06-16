"""Tests for custom music player failure cleanup."""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic
from discord.types.voice import VoiceServerUpdate as VoiceServerUpdatePayload

from api.music.player import MusicPlayer


class TestMusicPlayer(unittest.IsolatedAsyncioTestCase):
    async def test_voice_server_update_suppresses_client_connector_error(
        self,
    ) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            mafic.Player,
            "on_voice_server_update",
            new=AsyncMock(side_effect=aiohttp.ClientConnectionError("down")),
        ):
            await player.on_voice_server_update(
                cast(VoiceServerUpdatePayload, object())
            )

        player.cleanup.assert_called_once()

    async def test_voice_server_update_suppresses_http_not_found(self) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            mafic.Player,
            "on_voice_server_update",
            new=AsyncMock(side_effect=mafic.HTTPNotFound("Session not found")),
        ):
            await player.on_voice_server_update(
                cast(VoiceServerUpdatePayload, object())
            )

        player.cleanup.assert_called_once()

    async def test_update_does_not_call_remote_disconnect_after_http_not_found(
        self,
    ) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]
        player.disconnect = AsyncMock(  # type: ignore[method-assign]
            side_effect=mafic.HTTPNotFound("Session not found")
        )

        with patch.object(
            mafic.Player,
            "update",
            new=AsyncMock(side_effect=mafic.HTTPNotFound("Session not found")),
        ):
            with self.assertRaises(mafic.HTTPNotFound):
                await player.update(pause=True)

        player.cleanup.assert_called_once()
        player.disconnect.assert_not_awaited()
