"""Tests for music connection manager behaviors.
Covers node initialization, player retrieval, and join logic outcomes.
"""

import asyncio
import unittest
from typing import Any, override
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import mafic

from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    NodeNotConnectedError,
    VoiceCheckResult,
)
from api.music.player import MusicPlayer, music_player_factory
from api.music.service.connection_manager import ConnectionManager


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self):
        self.bot = MagicMock()
        self.bot.get_guild = MagicMock()
        self.manager = ConnectionManager(self.bot)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_initialize_connects_node(self, mock_pool_class: Any):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        node = MagicMock()

        with patch(
            "api.music.service.connection_manager.mafic.Node", return_value=node
        ) as node_class:
            manager = ConnectionManager(self.bot)

            await manager.initialize()

        self.assertTrue(manager._initialized)
        node_class.assert_called_once()
        mock_pool_instance.add_node.assert_awaited_once_with(
            node,
            player_cls=MusicPlayer,
        )

    async def test_get_player_returns_player(self):
        guild_mock = MagicMock()

        class DummyPlayer:
            pass

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            player_instance = DummyPlayer()
            guild_mock.voice_client = player_instance
            self.bot.get_guild.return_value = guild_mock
            self.manager.is_player_usable = MagicMock(return_value=True)

            player = self.manager.get_player(123)
            self.assertIsNotNone(player)
            self.assertIsInstance(player, DummyPlayer)

    async def test_get_player_hides_stale_player_when_node_unavailable(self):
        guild_mock = MagicMock()

        class DummyPlayer:
            pass

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            guild_mock.voice_client = DummyPlayer()
            self.bot.get_guild.return_value = guild_mock
            self.manager.is_player_usable = MagicMock(return_value=False)

            player = self.manager.get_player(123)

        self.assertIsNone(player)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_get_player_hides_player_with_node_not_in_pool(
        self, mock_pool_class: Any
    ):
        guild_mock = MagicMock()

        class DummyPlayer:
            def __init__(self, node: object) -> None:
                self._node = node

        mock_pool = MagicMock()
        mock_pool.nodes = []
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = DummyPlayer(MagicMock(available=True))
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            result = manager.get_player(123)

        self.assertIsNone(result)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_get_player_hides_player_with_unavailable_node(
        self, mock_pool_class: Any
    ):
        guild_mock = MagicMock()

        class DummyPlayer:
            def __init__(self, node: object) -> None:
                self._node = node

        node = MagicMock(available=False)
        mock_pool = MagicMock()
        mock_pool.nodes = [node]
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = DummyPlayer(node)
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            result = manager.get_player(123)

        self.assertIsNone(result)

    async def test_join_already_connected(self):
        guild = MagicMock()

        vc = MagicMock(spec=discord.VoiceClient)
        channel_mock = MagicMock(spec=discord.VoiceChannel)
        channel_mock.id = 100

        type(vc).channel = PropertyMock(return_value=channel_mock)
        guild.voice_client = vc

        channel_to_join = MagicMock(spec=discord.VoiceChannel)
        channel_to_join.id = 100

        res, old = await self.manager.join(guild, channel_to_join)
        self.assertEqual(res, VoiceCheckResult.ALREADY_CONNECTED)
        self.assertIsNone(old)

    async def test_join_returns_unavailable_without_connecting_voice(self):
        guild = MagicMock()
        guild.voice_client = None
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        self.manager.ensure_available = AsyncMock(return_value=False)

        res, old = await self.manager.join(guild, channel)

        self.assertEqual(res, VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        self.assertIsNone(old)
        channel.connect.assert_not_called()

    async def test_join_connects_new_player_when_service_is_available(self):
        guild = MagicMock()
        guild.voice_client = None
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        self.manager.ensure_available = AsyncMock(return_value=True)

        result = await self.manager.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.SUCCESS, None))
        channel.connect.assert_awaited_once_with(
            cls=music_player_factory,
            timeout=8.0,
        )

    async def test_join_cleans_stale_player_when_node_unavailable(self):
        class DummyPlayer:
            def __init__(self) -> None:
                self.disconnect = AsyncMock()

        player = DummyPlayer()
        guild = MagicMock()
        guild.voice_client = player
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        self.manager.has_ready_node = MagicMock(return_value=False)
        self.manager.ensure_available = AsyncMock(return_value=False)

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            res, old = await self.manager.join(guild, channel)

        self.assertEqual(res, VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        self.assertIsNone(old)
        player.disconnect.assert_awaited_once_with(force=True)
        channel.connect.assert_not_called()

    async def test_disconnect_suppresses_http_not_found_and_cleans_local_state(self):
        class DummyPlayer:
            def __init__(self) -> None:
                self.channel = MagicMock()
                self.disconnect = AsyncMock(side_effect=mafic.HTTPNotFound("missing"))
                self.cleanup = MagicMock()

        player = DummyPlayer()
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = player
        guild.change_voice_state = AsyncMock()

        with patch("api.music.service.connection_manager.MusicPlayer", DummyPlayer):
            await self.manager.disconnect(guild, force=True)

        player.disconnect.assert_awaited_once_with(force=True)
        guild.change_voice_state.assert_awaited_once_with(channel=None)
        player.cleanup.assert_called_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_repeated_ensure_available_uses_retry_cooldown(
        self, mock_pool_class: Any
    ):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(side_effect=RuntimeError("down"))
        mock_pool_class.return_value = mock_pool_instance
        node = MagicMock()
        node.close = AsyncMock()
        manager = ConnectionManager(self.bot)

        with patch(
            "api.music.service.connection_manager.mafic.Node", return_value=node
        ):
            first = await manager.ensure_available()
            second = await manager.ensure_available()

        self.assertFalse(first)
        self.assertFalse(second)
        mock_pool_instance.add_node.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_ensure_available_retries_after_cooldown(self, mock_pool_class: Any):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(side_effect=RuntimeError("down"))
        mock_pool_class.return_value = mock_pool_instance
        node = MagicMock()
        node.close = AsyncMock()
        manager = ConnectionManager(self.bot)

        with patch(
            "api.music.service.connection_manager.mafic.Node", return_value=node
        ):
            await manager.ensure_available()
            manager._next_connect_retry_at = 0.0
            await manager.ensure_available()

        self.assertEqual(mock_pool_instance.add_node.await_count, 2)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_initialize_closes_failed_node_and_raises_safe_message(
        self, mock_pool_class: Any
    ):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(
            side_effect=RuntimeError("ClientConnectorError localhost traceback")
        )
        mock_pool_class.return_value = mock_pool_instance
        node = MagicMock()
        node.close = AsyncMock()
        manager = ConnectionManager(self.bot)

        with patch(
            "api.music.service.connection_manager.mafic.Node", return_value=node
        ):
            with self.assertRaisesRegex(
                NodeNotConnectedError,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
            ) as ctx:
                await manager.initialize()

        self.assertNotIn("ClientConnectorError", str(ctx.exception))
        self.assertNotIn("localhost", str(ctx.exception))
        node.close.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_start_lazy_connect_schedules_one_background_attempt(
        self, mock_pool_class: Any
    ):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.close = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)
        manager.ensure_available = AsyncMock(return_value=False)

        manager.start_lazy_connect()
        manager.start_lazy_connect()
        await asyncio.sleep(0)

        manager.ensure_available.assert_awaited_once()
        await manager.cleanup()
        mock_pool_instance.close.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_cleanup_cancels_pending_lazy_connect(self, mock_pool_class: Any):
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.close = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)
        started = asyncio.Event()

        async def wait_forever() -> bool:
            started.set()
            await asyncio.Future()
            return True

        manager.ensure_available = AsyncMock(side_effect=wait_forever)

        manager.start_lazy_connect()
        await started.wait()
        await manager.cleanup()

        self.assertIsNone(manager._lazy_connect_task)
        mock_pool_instance.close.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_mark_node_unavailable_sets_cooldown_and_removes_node(
        self, mock_pool_class: Any
    ):
        node = MagicMock()
        mock_pool_instance = MagicMock()
        nodes = [node]
        mock_pool_instance.nodes = nodes
        mock_pool_instance.remove_node = AsyncMock()

        def remove_node_side_effect(*_args: Any, **_kwargs: Any) -> None:
            nodes.remove(node)

        mock_pool_instance.remove_node.side_effect = remove_node_side_effect
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)

        await manager.mark_node_unavailable(node)

        self.assertFalse(manager._initialized)
        self.assertTrue(manager.is_known_unavailable())
        self.assertGreater(manager._next_connect_retry_at, 0)
        mock_pool_instance.remove_node.assert_awaited_once_with(
            node, transfer_players=False
        )

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_mark_node_unavailable_closes_node_not_in_pool(
        self, mock_pool_class: Any
    ):
        node = MagicMock()
        node.close = AsyncMock()
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)

        await manager.mark_node_unavailable(node)

        node.close.assert_awaited_once()
