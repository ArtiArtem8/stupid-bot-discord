"""Tests for music connection manager behaviors.
Covers node initialization, player retrieval, and join logic outcomes.
"""

import asyncio
import unittest
from typing import Any, cast, override
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import mafic

from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    NodeNotConnectedError,
    VoiceCheckResult,
)
from api.music.player import music_player_factory
from api.music.service.connection_manager import ConnectionManager


class _FakeMusicPlayer:
    def __init__(self, guild: MagicMock, node: object | None = None) -> None:
        self.guild = guild
        self._node = node
        self._is_stale = False

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    def mark_stale(self) -> None:
        self._is_stale = True


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self):
        self.bot = MagicMock()
        self.bot.get_guild = MagicMock()
        self.manager = ConnectionManager(self.bot)
        player_patch = patch(
            "api.music.service.connection_manager.MusicPlayer", _FakeMusicPlayer
        )
        player_patch.start()
        self.addCleanup(player_patch.stop)

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
            player_cls=_FakeMusicPlayer,
        )

    async def test_get_player_returns_player(self):
        guild_mock = MagicMock()
        player_instance = _FakeMusicPlayer(guild_mock)
        guild_mock.voice_client = player_instance
        self.bot.get_guild.return_value = guild_mock
        is_player_usable = MagicMock(return_value=True)

        with patch.object(self.manager, "is_player_usable", is_player_usable):
            player = self.manager.get_player(123)

        self.assertIs(player, player_instance)

    async def test_get_player_hides_stale_player_when_node_unavailable(self):
        guild_mock = MagicMock()
        guild_mock.voice_client = _FakeMusicPlayer(guild_mock)
        self.bot.get_guild.return_value = guild_mock
        is_player_usable = MagicMock(return_value=False)

        with patch.object(self.manager, "is_player_usable", is_player_usable):
            player = self.manager.get_player(123)

        self.assertIsNone(player)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_get_player_hides_player_with_node_not_in_pool(
        self, mock_pool_class: Any
    ):
        guild_mock = MagicMock()

        mock_pool = MagicMock()
        mock_pool.nodes = []
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(guild_mock, MagicMock(available=True))
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        result = manager.get_player(123)

        self.assertIsNone(result)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_get_player_hides_player_with_unavailable_node(
        self, mock_pool_class: Any
    ):
        guild_mock = MagicMock()

        node = MagicMock(available=False)
        mock_pool = MagicMock()
        mock_pool.nodes = [node]
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(guild_mock, node)
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        result = manager.get_player(123)

        self.assertIsNone(result)

    def test_registered_non_stale_player_is_current(self) -> None:
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild

        result = self.manager.is_current_player(player)

        self.assertTrue(result)
        self.bot.get_guild.assert_called_once_with(123)

    def test_registered_stale_player_is_not_current(self) -> None:
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild)
        player.mark_stale()
        guild.voice_client = player
        self.bot.get_guild.return_value = guild

        result = self.manager.is_current_player(player)

        self.assertFalse(result)

    def test_other_player_for_same_guild_is_not_current(self) -> None:
        guild = MagicMock(id=123)
        current_player = _FakeMusicPlayer(guild)
        other_player = _FakeMusicPlayer(guild)
        guild.voice_client = current_player
        self.bot.get_guild.return_value = guild

        result = self.manager.is_current_player(other_player)

        self.assertFalse(result)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    def test_non_current_player_is_not_usable_with_available_node(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123)
        node = MagicMock(available=True)
        current_player = _FakeMusicPlayer(guild, node)
        other_player = _FakeMusicPlayer(guild, node)
        guild.voice_client = current_player
        self.bot.get_guild.return_value = guild
        mock_pool_class.return_value.nodes = [node]
        manager = ConnectionManager(self.bot)

        result = manager.is_player_usable(other_player)

        self.assertFalse(result)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    def test_current_player_with_available_node_is_usable(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123)
        node = MagicMock(available=True)
        player = _FakeMusicPlayer(guild, node)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        mock_pool_class.return_value.nodes = [node]
        manager = ConnectionManager(self.bot)

        result = manager.is_player_usable(player)

        self.assertTrue(result)

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
        ensure_available = AsyncMock(return_value=False)

        with patch.object(self.manager, "ensure_available", ensure_available):
            res, old = await self.manager.join(guild, channel)

        self.assertEqual(res, VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        self.assertIsNone(old)
        channel.connect.assert_not_called()

    async def test_join_connects_new_player_when_service_is_available(self):
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = None
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        ensure_available = AsyncMock(return_value=True)

        with patch.object(self.manager, "ensure_available", ensure_available):
            result = await self.manager.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.SUCCESS, None))
        channel.connect.assert_awaited_once_with(
            cls=music_player_factory,
            timeout=8.0,
        )

    async def test_concurrent_join_same_guild_does_not_overlap_join_body(self) -> None:
        guild = MagicMock(id=123)
        channel = MagicMock(spec=discord.VoiceChannel)
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0

        async def join_body(
            _guild: discord.Guild,
            _channel: discord.VoiceChannel | discord.StageChannel,
        ) -> tuple[VoiceCheckResult, None]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            entered.set()
            await release.wait()
            active -= 1
            return VoiceCheckResult.SUCCESS, None

        with patch.object(self.manager, "_join_unlocked", side_effect=join_body):
            first = asyncio.create_task(self.manager.join(guild, channel))
            await entered.wait()
            second = asyncio.create_task(self.manager.join(guild, channel))
            await asyncio.sleep(0)
            self.assertEqual(max_active, 1)
            release.set()

            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(first_result, (VoiceCheckResult.SUCCESS, None))
        self.assertEqual(second_result, (VoiceCheckResult.SUCCESS, None))
        self.assertEqual(max_active, 1)

    async def test_join_cleans_stale_player_when_node_unavailable(self):
        guild = MagicMock()
        player = _FakeMusicPlayer(guild)
        disconnect = AsyncMock()
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        has_ready_node = MagicMock(return_value=False)
        ensure_available = AsyncMock(return_value=False)

        with (
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(self.manager, "has_ready_node", has_ready_node),
            patch.object(self.manager, "ensure_available", ensure_available),
        ):
            res, old = await self.manager.join(guild, channel)

        self.assertEqual(res, VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        self.assertIsNone(old)
        disconnect.assert_awaited_once_with(force=True)
        channel.connect.assert_not_called()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_stale_player_is_unusable_even_when_node_available(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123)
        node = MagicMock(available=True)
        mock_pool = MagicMock()
        mock_pool.nodes = [node]
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(guild, node)
        player.mark_stale()

        result = manager.is_player_usable(player)

        self.assertFalse(result)

    async def test_detach_marks_player_stale_when_remote_disconnect_fails(
        self,
    ) -> None:
        guild = MagicMock()
        player = _FakeMusicPlayer(guild)
        disconnect = AsyncMock(side_effect=mafic.HTTPNotFound("missing"))
        cleanup = MagicMock()
        guild.change_voice_state = AsyncMock()

        with (
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(player, "cleanup", cleanup, create=True),
        ):
            await self.manager.detach_stale_voice_client(
                guild, cast(discord.VoiceProtocol, cast(object, player))
            )

        self.assertTrue(player.is_stale)
        disconnect.assert_awaited_once_with(force=True)
        guild.change_voice_state.assert_awaited_once_with(channel=None)
        cleanup.assert_called_once()

    async def test_disconnect_suppresses_http_not_found_and_cleans_local_state(self):
        guild = MagicMock()
        guild.id = 123
        player = _FakeMusicPlayer(guild)
        disconnect = AsyncMock(side_effect=mafic.HTTPNotFound("missing"))
        cleanup = MagicMock()
        channel = MagicMock()
        guild.voice_client = player
        guild.change_voice_state = AsyncMock()
        self.bot.get_guild.return_value = guild

        with (
            patch.object(player, "channel", channel, create=True),
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(player, "cleanup", cleanup, create=True),
        ):
            await self.manager.disconnect(guild, force=True)

        disconnect.assert_awaited_once_with(force=True)
        guild.change_voice_state.assert_awaited_once_with(channel=None)
        cleanup.assert_called_once()

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
        ensure_available = AsyncMock(return_value=False)

        with patch.object(manager, "ensure_available", ensure_available):
            manager.start_lazy_connect()
            manager.start_lazy_connect()
            await asyncio.sleep(0)

        ensure_available.assert_awaited_once()
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

        ensure_available = AsyncMock(side_effect=wait_forever)

        with patch.object(manager, "ensure_available", ensure_available):
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
