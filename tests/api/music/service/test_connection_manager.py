import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord

from api.music.models import VoiceCheckResult
from api.music.service.connection_manager import ConnectionManager


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.get_guild = MagicMock()
        self.manager = ConnectionManager(self.bot)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_initialize_connects_node(self, mock_pool_class: Any):
        mock_pool_instance = MagicMock()
        mock_pool_instance.create_node = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance

        # Create a new manager with the patched NodePool
        manager = ConnectionManager(self.bot)

        await manager.initialize()

        self.assertTrue(manager._initialized)  # pyright: ignore[reportPrivateUsage]
        mock_pool_instance.create_node.assert_called_once()

    async def test_get_player_returns_player(self):
        guild_mock = MagicMock()

        # Create a proper mock player
        class DummyPlayer:
            pass

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            player_instance = DummyPlayer()
            guild_mock.voice_client = player_instance
            self.bot.get_guild.return_value = guild_mock

            player = self.manager.get_player(123)
            self.assertIsNotNone(player)
            self.assertIsInstance(player, DummyPlayer)

    async def test_join_already_connected(self):
        guild = MagicMock()

        # Create proper voice channel mocks
        vc = MagicMock(spec=discord.VoiceClient)
        channel_mock = MagicMock(spec=discord.VoiceChannel)
        channel_mock.id = 100

        # Use type() to set read-only property
        type(vc).channel = PropertyMock(return_value=channel_mock)
        guild.voice_client = vc

        channel_to_join = MagicMock(spec=discord.VoiceChannel)
        channel_to_join.id = 100

        res, old = await self.manager.join(guild, channel_to_join)
        self.assertEqual(res, VoiceCheckResult.ALREADY_CONNECTED)
        self.assertIsNone(old)
