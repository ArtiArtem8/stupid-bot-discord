"""Tests for music connection manager behavior."""

import asyncio
import ssl
import time
import unittest
from typing import Any, cast, override
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import aiohttp
import discord
import mafic

import config
from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    NodeNotConnectedError,
    VoiceCheckResult,
)
from api.music.player import MusicPlayer, music_player_factory
from api.music.service import connection_manager as connection_module
from api.music.service.connection_manager import ConnectionManager, _AvailabilityResult


class _FakeMusicPlayer:
    def __init__(
        self,
        guild: MagicMock,
        node: object | None = None,
        *,
        connected: bool = True,
    ) -> None:
        self.guild = guild
        self.assigned_node = node
        self._is_stale = False
        self.connected = connected

    @property
    def is_stale(self) -> bool:
        return self._is_stale

    def mark_stale(self) -> None:
        self._is_stale = True

    async def transfer_to(self, node: object) -> None:
        self.assigned_node = node


def _as_music_player(player: _FakeMusicPlayer) -> MusicPlayer:
    return cast(MusicPlayer, cast(object, player))


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.bot.get_guild = MagicMock()
        self.manager = ConnectionManager(self.bot)
        player_patch = patch(
            "api.music.service.connection_manager.MusicPlayer", _FakeMusicPlayer
        )
        player_patch.start()
        self.addCleanup(player_patch.stop)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_initialize_connects_node(self, mock_pool_class: Any) -> None:
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

    async def test_get_player_returns_player(self) -> None:
        guild_mock = MagicMock()
        player_instance = _FakeMusicPlayer(guild_mock)
        guild_mock.voice_client = player_instance
        self.bot.get_guild.return_value = guild_mock
        is_player_usable = MagicMock(return_value=True)

        with patch.object(self.manager, "is_player_usable", is_player_usable):
            player = self.manager.get_player(123)

        self.assertIs(player, player_instance)

    async def test_get_player_hides_stale_player_when_node_unavailable(self) -> None:
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
    ) -> None:
        guild_mock = MagicMock()

        mock_pool = MagicMock()
        mock_pool.nodes = []
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(
            guild_mock,
            MagicMock(label="orphaned", available=True),
        )
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        with self.assertLogs(
            "api.music.service.connection_manager",
            level="WARNING",
        ) as captured:
            result = manager.get_player(
                123,
                failure_context="successful_join_missing_player",
            )

        self.assertIsNone(result)
        warning = captured.records[0].getMessage()
        self.assertIn("Player unusable guild=123", warning)
        self.assertIn("player_connected=True", warning)
        self.assertIn("node_label=orphaned", warning)
        self.assertIn("node_in_pool=False", warning)
        self.assertIn("node_available=True", warning)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_get_player_hides_player_with_unavailable_node(
        self, mock_pool_class: Any
    ) -> None:
        guild_mock = MagicMock()

        node = MagicMock(label="down", available=False)
        mock_pool = MagicMock()
        mock_pool.nodes = [node]
        mock_pool.label_to_node = {"down": node}
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(guild_mock, node)
        guild_mock.voice_client = player
        self.bot.get_guild.return_value = guild_mock

        with self.assertLogs(
            "api.music.service.connection_manager",
            level="WARNING",
        ) as captured:
            result = manager.get_player(
                123,
                failure_context="successful_join_missing_player",
            )

        self.assertIsNone(result)
        warning = captured.records[0].getMessage()
        self.assertIn("node_label=down", warning)
        self.assertIn("node_in_pool=True", warning)
        self.assertIn("node_available=False", warning)

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
        node = MagicMock(label="MAIN", available=True)
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

    @patch("api.music.service.connection_manager.mafic.NodePool")
    def test_disconnected_current_player_is_not_usable(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123)
        node = MagicMock(available=True)
        player = _FakeMusicPlayer(guild, node, connected=False)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        mock_pool_class.return_value.nodes = [node]
        manager = ConnectionManager(self.bot)

        result = manager.is_player_usable(player)

        self.assertFalse(result)

    async def test_join_already_connected(self) -> None:
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

    async def test_join_returns_unavailable_without_connecting_voice(self) -> None:
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

    async def test_join_connects_new_player_when_service_is_available(self) -> None:
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = None
        player = _FakeMusicPlayer(guild)
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock(return_value=player)
        ensure_available = AsyncMock(return_value=True)

        with (
            patch.object(self.manager, "ensure_available", ensure_available),
            patch.object(self.manager, "is_player_usable", return_value=True),
        ):
            result = await self.manager.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.SUCCESS, None))
        channel.connect.assert_awaited_once_with(
            cls=music_player_factory,
            timeout=8.0,
        )

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_join_rejects_disconnected_player_returned_by_connect(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123, voice_client=None)
        node = MagicMock(label="MAIN", available=True)
        player = _FakeMusicPlayer(guild, node, connected=False)
        self.bot.get_guild.return_value = guild
        mock_pool_class.return_value.nodes = [node]
        manager = ConnectionManager(self.bot)
        channel = MagicMock(spec=discord.VoiceChannel)

        async def connect(**_kwargs: object) -> _FakeMusicPlayer:
            guild.voice_client = player
            return player

        channel.connect = AsyncMock(side_effect=connect)
        invalidate_player = AsyncMock()

        with (
            patch.object(manager, "invalidate_player", invalidate_player),
            self.assertLogs(
                "api.music.service.connection_manager",
                level="WARNING",
            ) as captured,
        ):
            result = await manager.join(guild, channel)

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        invalidate_player.assert_awaited_once_with(player)
        warning = captured.records[0].getMessage()
        self.assertIn("context=fresh_connect_validation", warning)
        self.assertIn("player_stale=False", warning)
        self.assertIn("is_current_voice_client=True", warning)
        self.assertIn("player_connected=False", warning)
        self.assertIn("node_label=MAIN", warning)
        self.assertIn("node_in_pool=True", warning)
        self.assertIn("node_available=True", warning)

    async def test_connect_timeout_does_not_invalidate_node(self) -> None:
        guild = MagicMock(id=123, voice_client=None)
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock(side_effect=TimeoutError("timed out"))
        ensure_available = AsyncMock(return_value=True)
        detach_failed_connect = AsyncMock()
        mark_node_unavailable = AsyncMock()

        with (
            patch.object(self.manager, "ensure_available", ensure_available),
            patch.object(
                self.manager,
                "_detach_voice_client_after_failed_connect",
                detach_failed_connect,
            ),
            patch.object(self.manager, "mark_node_unavailable", mark_node_unavailable),
        ):
            result = await self.manager.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.TIMEOUT, None))
        detach_failed_connect.assert_awaited_once_with(guild)
        mark_node_unavailable.assert_not_awaited()

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

    async def test_join_propagates_unexpected_connect_failure(self) -> None:
        guild = MagicMock(id=123, voice_client=None)
        channel = MagicMock(spec=discord.VoiceChannel)
        error = RuntimeError("programming failure")
        channel.connect = AsyncMock(side_effect=error)

        with (
            patch.object(
                self.manager,
                "ensure_available",
                new=AsyncMock(return_value=True),
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            await self.manager.join(guild, channel)

        self.assertIs(raised.exception, error)

    async def test_join_translates_discord_client_failure(self) -> None:
        guild = MagicMock(id=123, voice_client=None)
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock(
            side_effect=discord.ClientException("already connected")
        )
        detach = AsyncMock()

        with (
            patch.object(
                self.manager,
                "ensure_available",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                self.manager,
                "_detach_voice_client_after_failed_connect",
                detach,
            ),
        ):
            result = await self.manager.join(guild, channel)

        self.assertEqual(result, (VoiceCheckResult.CONNECTION_FAILED, None))
        detach.assert_awaited_once_with(guild)

    async def test_lazy_connect_logs_unexpected_failure_at_boundary(self) -> None:
        with (
            patch.object(
                self.manager,
                "ensure_available",
                new=AsyncMock(side_effect=RuntimeError("programming failure")),
            ),
            self.assertLogs(
                "api.music.service.connection_manager",
                level="ERROR",
            ),
        ):
            await self.manager._run_lazy_connect()

    async def test_join_cleans_stale_player_when_node_unavailable(self) -> None:
        guild = MagicMock()
        player = _FakeMusicPlayer(guild)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        has_ready_node = MagicMock(return_value=False)
        ensure_available = AsyncMock(return_value=False)
        invalidate_player = AsyncMock()
        mark_node_unavailable = AsyncMock()

        with (
            patch.object(self.manager, "has_ready_node", has_ready_node),
            patch.object(self.manager, "ensure_available", ensure_available),
            patch.object(self.manager, "invalidate_player", invalidate_player),
            patch.object(self.manager, "mark_node_unavailable", mark_node_unavailable),
        ):
            res, old = await self.manager.join(guild, channel)

        self.assertEqual(res, VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        self.assertIsNone(old)
        invalidate_player.assert_awaited_once_with(player)
        mark_node_unavailable.assert_not_awaited()
        channel.connect.assert_not_called()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_invalidate_player_is_local_to_the_passed_player(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock()
        node = MagicMock(label="ready", available=True)
        mock_pool = MagicMock()
        mock_pool.nodes = [node]
        mock_pool.label_to_node = {"ready": node}
        mock_pool_class.return_value = mock_pool
        manager = ConnectionManager(self.bot)
        player: Any = _FakeMusicPlayer(guild, node)
        other_player = _FakeMusicPlayer(MagicMock(), node)

        async def assert_stale_before_detach(
            actual_guild: object, actual_player: object
        ) -> None:
            self.assertTrue(player.is_stale)
            self.assertIs(actual_guild, guild)
            self.assertIs(actual_player, player)
            self.assertFalse(other_player.is_stale)

        detach_stale_voice_client = AsyncMock(side_effect=assert_stale_before_detach)
        mark_node_unavailable = AsyncMock()

        with (
            patch.object(manager, "mark_node_unavailable", mark_node_unavailable),
            patch.object(
                manager,
                "detach_stale_voice_client",
                detach_stale_voice_client,
            ),
        ):
            await manager.invalidate_player(_as_music_player(player))

        detach_stale_voice_client.assert_awaited_once_with(guild, player)
        mark_node_unavailable.assert_not_awaited()
        self.assertEqual(mock_pool.nodes, [node])
        self.assertIs(mock_pool.label_to_node["ready"], node)
        self.assertFalse(other_player.is_stale)

    async def test_invalidate_node_and_players_marks_stale_before_node_cleanup(
        self,
    ) -> None:
        guild = MagicMock()
        node = MagicMock()
        player: Any = _FakeMusicPlayer(guild, node)
        other_player: Any = _FakeMusicPlayer(MagicMock(), node)
        node.players = [other_player]
        calls: list[str] = []

        def record_node(actual_node: object) -> None:
            self.assertIs(actual_node, node)
            self.assertTrue(player.is_stale)
            self.assertTrue(other_player.is_stale)
            calls.append("node")

        def record_local_cleanup(_guild: object, actual_player: object) -> None:
            calls.append("failed" if actual_player is player else "other")

        mark_node_unavailable = AsyncMock(side_effect=record_node)
        local_cleanup = AsyncMock(side_effect=record_local_cleanup)
        player_disconnect = AsyncMock()
        other_disconnect = AsyncMock()

        with (
            patch.object(self.manager, "mark_node_unavailable", mark_node_unavailable),
            patch.object(
                self.manager,
                "_cleanup_voice_client_locally",
                local_cleanup,
            ),
            patch.object(player, "disconnect", player_disconnect, create=True),
            patch.object(other_player, "disconnect", other_disconnect, create=True),
        ):
            await self.manager.invalidate_node_and_players(player)

        mark_node_unavailable.assert_awaited_once_with(node)
        self.assertEqual(
            local_cleanup.await_args_list,
            [call(other_player.guild, other_player), call(player.guild, player)],
        )
        self.assertEqual(calls, ["node", "other", "failed"])
        player_disconnect.assert_not_awaited()
        other_disconnect.assert_not_awaited()

    async def test_invalidate_node_and_players_detaches_after_node_failure(
        self,
    ) -> None:
        node = MagicMock()
        player: Any = _FakeMusicPlayer(MagicMock(), node)
        other_player: Any = _FakeMusicPlayer(MagicMock(), node)
        node.players = [other_player]
        error = RuntimeError("node cleanup failed")

        async def fail_node_cleanup(_node: object) -> None:
            self.assertTrue(player.is_stale)
            self.assertTrue(other_player.is_stale)
            raise error

        mark_node_unavailable = AsyncMock(side_effect=fail_node_cleanup)
        local_cleanup = AsyncMock()
        player_disconnect = AsyncMock()
        other_disconnect = AsyncMock()

        with (
            patch.object(self.manager, "mark_node_unavailable", mark_node_unavailable),
            patch.object(
                self.manager,
                "_cleanup_voice_client_locally",
                local_cleanup,
            ),
            patch.object(player, "disconnect", player_disconnect, create=True),
            patch.object(other_player, "disconnect", other_disconnect, create=True),
            self.assertRaises(RuntimeError) as raised,
        ):
            await self.manager.invalidate_node_and_players(player)

        self.assertIs(raised.exception, error)
        self.assertEqual(
            local_cleanup.await_args_list,
            [call(other_player.guild, other_player), call(player.guild, player)],
        )
        player_disconnect.assert_not_awaited()
        other_disconnect.assert_not_awaited()

    async def test_join_move_transport_failure_uses_only_player_scope(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        old_channel = MagicMock(spec=discord.VoiceChannel, id=100)
        new_channel = MagicMock(spec=discord.VoiceChannel, id=200)
        player = _FakeMusicPlayer(guild)
        move_to = AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
        guild.voice_client = player
        invalidate_node_and_players = AsyncMock()
        invalidate_player = AsyncMock()

        with (
            patch.object(player, "channel", old_channel, create=True),
            patch.object(player, "move_to", move_to, create=True),
            patch.object(self.manager, "is_player_usable", return_value=True),
            patch.object(
                self.manager,
                "invalidate_node_and_players",
                invalidate_node_and_players,
            ),
            patch.object(self.manager, "invalidate_player", invalidate_player),
        ):
            result = await self.manager.join(guild, new_channel)

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        invalidate_player.assert_awaited_once_with(
            player,
            context="voice_move_io_failure",
            error="ClientConnectionError",
        )
        invalidate_node_and_players.assert_not_awaited()

    async def test_move_timeout_uses_only_player_scope(self) -> None:
        guild = MagicMock(id=123)
        old_channel = MagicMock(spec=discord.VoiceChannel, id=100)
        new_channel = MagicMock(spec=discord.VoiceChannel, id=200)
        player = _FakeMusicPlayer(guild)
        move_to = AsyncMock(side_effect=TimeoutError("timed out"))
        guild.voice_client = player
        invalidate_node_and_players = AsyncMock()
        invalidate_player = AsyncMock()

        with (
            patch.object(player, "channel", old_channel, create=True),
            patch.object(player, "move_to", move_to, create=True),
            patch.object(self.manager, "is_player_usable", return_value=True),
            patch.object(
                self.manager,
                "invalidate_node_and_players",
                invalidate_node_and_players,
            ),
            patch.object(self.manager, "invalidate_player", invalidate_player),
        ):
            result = await self.manager.join(guild, new_channel)

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        invalidate_player.assert_awaited_once_with(
            player,
            context="voice_move_io_failure",
            error="TimeoutError",
        )
        invalidate_node_and_players.assert_not_awaited()

    async def test_join_transport_failure_uses_only_player_scope(self) -> None:
        guild = MagicMock()
        player = _FakeMusicPlayer(guild)
        guild.voice_client = player
        invalidate_node_and_players = AsyncMock()
        invalidate_player = AsyncMock()

        with (
            patch.object(
                self.manager,
                "invalidate_node_and_players",
                invalidate_node_and_players,
            ),
            patch.object(self.manager, "invalidate_player", invalidate_player),
        ):
            await self.manager._handle_join_io_failure(
                guild, aiohttp.ClientConnectionError("down")
            )

        invalidate_player.assert_awaited_once_with(
            player,
            context="voice_join_io_failure",
            error="ClientConnectionError",
        )
        invalidate_node_and_players.assert_not_awaited()

    async def test_move_race_invalidates_only_player(self) -> None:
        guild = MagicMock(id=123)
        old_channel = MagicMock(spec=discord.VoiceChannel)
        new_channel = MagicMock(spec=discord.VoiceChannel)
        player: Any = _FakeMusicPlayer(guild, MagicMock(available=True))
        invalidate_player = AsyncMock()
        mark_node_unavailable = AsyncMock()

        with (
            patch.object(player, "channel", old_channel, create=True),
            patch.object(self.manager, "is_player_usable", return_value=False),
            patch.object(self.manager, "invalidate_player", invalidate_player),
            patch.object(self.manager, "mark_node_unavailable", mark_node_unavailable),
            self.assertLogs(
                "api.music.service.connection_manager",
                level="WARNING",
            ) as captured,
        ):
            result = await self.manager._reuse_or_move_player(
                _as_music_player(player), new_channel
            )

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        invalidate_player.assert_awaited_once_with(player)
        warning = captured.records[0].getMessage()
        self.assertIn("context=pre_move_validation", warning)
        self.assertIn("is_current_voice_client=False", warning)
        mark_node_unavailable.assert_not_awaited()

    async def test_move_that_loses_readiness_returns_unavailable(self) -> None:
        guild = MagicMock(id=123)
        old_channel = MagicMock(spec=discord.VoiceChannel)
        new_channel = MagicMock(spec=discord.VoiceChannel)
        player = _FakeMusicPlayer(guild, MagicMock(available=True))
        move_to = AsyncMock()
        invalidate_player = AsyncMock()

        with (
            patch.object(player, "channel", old_channel, create=True),
            patch.object(player, "move_to", move_to, create=True),
            patch.object(
                self.manager,
                "is_player_usable",
                side_effect=(True, False),
            ),
            patch.object(
                self.manager,
                "invalidate_player",
                invalidate_player,
            ),
        ):
            result = await self.manager._reuse_or_move_player(
                _as_music_player(player), new_channel
            )

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        move_to.assert_awaited_once_with(new_channel, timeout=5.0)
        invalidate_player.assert_awaited_once_with(player)

    async def test_disconnect_timeout_uses_only_player_scope(self) -> None:
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild, MagicMock())
        channel = MagicMock(spec=discord.VoiceChannel)
        disconnect = AsyncMock(side_effect=TimeoutError("timed out"))
        guild.voice_client = player
        invalidate_node_and_players = AsyncMock()
        invalidate_player = AsyncMock()

        with (
            patch.object(player, "channel", channel, create=True),
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(self.manager, "is_player_usable", return_value=True),
            patch.object(
                self.manager,
                "invalidate_node_and_players",
                invalidate_node_and_players,
            ),
            patch.object(self.manager, "invalidate_player", invalidate_player),
        ):
            await self.manager.disconnect(guild, force=True)

        invalidate_player.assert_awaited_once_with(
            player,
            context="voice_disconnect_io_failure",
            error="TimeoutError",
        )
        invalidate_node_and_players.assert_not_awaited()

    async def test_disconnect_returns_true_without_voice_client(self) -> None:
        guild = MagicMock(id=123)
        guild.voice_client = None

        result = await self.manager.disconnect(guild)

        self.assertIs(result, True)

    async def test_disconnect_returns_true_when_disconnect_clears_cache(self) -> None:
        guild = MagicMock(id=123)
        voice_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = voice_client

        async def disconnect(*, force: bool) -> None:
            self.assertTrue(force)
            guild.voice_client = None

        voice_client.disconnect = AsyncMock(side_effect=disconnect)

        result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, True)
        voice_client.disconnect.assert_awaited_once_with(force=True)
        voice_client.cleanup.assert_not_called()

    async def test_disconnect_returns_true_when_cleanup_clears_cache(self) -> None:
        guild = MagicMock(id=123)
        voice_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = voice_client
        voice_client.disconnect = AsyncMock()
        voice_client.cleanup.side_effect = lambda: setattr(guild, "voice_client", None)

        result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, True)
        voice_client.disconnect.assert_awaited_once_with(force=True)
        voice_client.cleanup.assert_called_once()

    async def test_disconnect_returns_true_after_expected_lavalink_failure(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild, MagicMock())
        guild.voice_client = player
        disconnect = AsyncMock(side_effect=mafic.HTTPNotFound("missing"))

        async def invalidate_player(
            _: MusicPlayer,
            **_kwargs: object,
        ) -> None:
            guild.voice_client = None

        with (
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(self.manager, "is_player_usable", return_value=True),
            patch.object(
                self.manager,
                "invalidate_player",
                AsyncMock(side_effect=invalidate_player),
            ) as invalidate,
        ):
            result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, True)
        disconnect.assert_awaited_once_with(force=True)
        invalidate.assert_awaited_once_with(
            player,
            context="voice_disconnect_io_failure",
            error="HTTPNotFound",
        )

    async def test_disconnect_returns_true_after_unexpected_failure_cleanup(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        voice_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = voice_client
        voice_client.disconnect = AsyncMock(side_effect=RuntimeError("failed"))

        async def detach(
            _guild: discord.Guild, _voice_client: discord.VoiceProtocol
        ) -> None:
            guild.voice_client = None

        with patch.object(
            self.manager,
            "detach_stale_voice_client",
            AsyncMock(side_effect=detach),
        ) as detach_stale:
            result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, True)
        detach_stale.assert_awaited_once_with(guild, voice_client)

    async def test_disconnect_returns_true_after_unusable_player_detach(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild, MagicMock())
        guild.voice_client = player

        async def detach(_guild: discord.Guild, _player: MusicPlayer) -> None:
            guild.voice_client = None

        with (
            patch.object(self.manager, "is_player_usable", return_value=False),
            patch.object(
                self.manager,
                "_detach_unusable_player",
                AsyncMock(side_effect=detach),
            ) as detach_unusable,
        ):
            result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, True)
        detach_unusable.assert_awaited_once_with(guild, player)

    async def test_disconnect_returns_false_when_cleanup_keeps_voice_client(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        voice_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = voice_client
        voice_client.disconnect = AsyncMock()

        result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, False)
        self.assertIs(guild.voice_client, voice_client)

    async def test_disconnect_returns_false_when_voice_client_is_replaced(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        old_client = MagicMock(spec=discord.VoiceProtocol)
        new_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = old_client

        async def disconnect(*, force: bool) -> None:
            self.assertTrue(force)
            guild.voice_client = new_client

        def destructive_old_cleanup() -> None:
            guild.voice_client = None

        old_client.disconnect = AsyncMock(side_effect=disconnect)
        old_client.cleanup.side_effect = destructive_old_cleanup

        result = await self.manager.disconnect(guild, force=True)

        self.assertIs(result, False)
        self.assertIs(guild.voice_client, new_client)
        old_client.disconnect.assert_awaited_once_with(force=True)
        old_client.cleanup.assert_not_called()

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
        guild.voice_client = player
        self.bot.get_guild.return_value = guild

        with self.assertNoLogs(
            "api.music.service.connection_manager",
            level="WARNING",
        ):
            result = manager.is_player_usable(player)

        self.assertFalse(result)

        with self.assertLogs(
            "api.music.service.connection_manager",
            level="WARNING",
        ) as captured:
            current = manager.get_player(
                guild.id,
                failure_context="successful_join_missing_player",
            )

        self.assertIsNone(current)
        warning = captured.records[0].getMessage()
        self.assertIn("player_stale=True", warning)
        self.assertIn("is_current_voice_client=True", warning)
        self.assertIn("player_connected=True", warning)
        self.assertIn("node_in_pool=True", warning)

    async def test_detach_marks_player_stale_when_remote_disconnect_fails(
        self,
    ) -> None:
        guild = MagicMock()
        player = _FakeMusicPlayer(guild)
        guild.voice_client = player
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

    async def test_invalidate_player_disconnects_current_once_without_repeat_cleanup(
        self,
    ) -> None:
        guild = MagicMock()
        player: Any = _FakeMusicPlayer(guild)
        guild.voice_client = player

        async def disconnect_current(*, force: bool) -> None:
            self.assertTrue(force)
            guild.voice_client = None

        disconnect = AsyncMock(side_effect=disconnect_current)
        cleanup = MagicMock()
        guild.change_voice_state = AsyncMock()

        with (
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(player, "cleanup", cleanup, create=True),
        ):
            await self.manager.invalidate_player(_as_music_player(player))

        self.assertTrue(player.is_stale)
        disconnect.assert_awaited_once_with(force=True)
        guild.change_voice_state.assert_not_awaited()
        cleanup.assert_not_called()

    async def test_invalidate_player_skips_client_absent_from_guild_cache(self) -> None:
        guild = MagicMock()
        player: Any = _FakeMusicPlayer(guild)
        guild.voice_client = None
        disconnect = AsyncMock()
        cleanup = MagicMock()
        guild.change_voice_state = AsyncMock()

        with (
            patch.object(player, "disconnect", disconnect, create=True),
            patch.object(player, "cleanup", cleanup, create=True),
        ):
            await self.manager.invalidate_player(_as_music_player(player))

        self.assertTrue(player.is_stale)
        disconnect.assert_not_awaited()
        guild.change_voice_state.assert_not_awaited()
        cleanup.assert_not_called()

    async def test_invalidate_old_player_preserves_new_voice_client(self) -> None:
        guild = MagicMock()
        old_player: Any = _FakeMusicPlayer(guild)
        new_player = _FakeMusicPlayer(guild)
        guild.voice_client = new_player
        disconnect = AsyncMock()
        cleanup = MagicMock()
        guild.change_voice_state = AsyncMock()

        with (
            patch.object(old_player, "disconnect", disconnect, create=True),
            patch.object(old_player, "cleanup", cleanup, create=True),
        ):
            await self.manager.invalidate_player(_as_music_player(old_player))

        self.assertTrue(old_player.is_stale)
        self.assertIs(guild.voice_client, new_player)
        disconnect.assert_not_awaited()
        guild.change_voice_state.assert_not_awaited()
        cleanup.assert_not_called()

    async def test_disconnect_suppresses_http_not_found_and_cleans_local_state(
        self,
    ) -> None:
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
    ) -> None:
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("down")
        )
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

    async def test_retry_cooldown_logs_last_error_and_remaining_delay(self) -> None:
        self.manager._last_connect_error = "ClientConnectorError"
        self.manager._next_connect_retry_at = 105.5

        with (
            patch(
                "api.music.service.connection_manager.time.monotonic",
                return_value=100.0,
            ),
            self.assertLogs(
                "api.music.service.connection_manager",
                level="DEBUG",
            ) as captured,
        ):
            result = await self.manager.ensure_available()

        self.assertFalse(result)
        message = captured.records[0].getMessage()
        self.assertIn("last_connect_error=ClientConnectorError", message)
        self.assertIn("retry_in_seconds=5.5", message)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_ensure_available_retries_after_cooldown(
        self, mock_pool_class: Any
    ) -> None:
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("down")
        )
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
    ) -> None:
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.add_node = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("localhost connection failed")
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
    ) -> None:
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.close = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)
        check_availability = AsyncMock(
            side_effect=(
                _AvailabilityResult.RETRY_LATER,
                _AvailabilityResult.RETRY_LATER,
                _AvailabilityResult.READY,
            )
        )
        manager._next_connect_retry_at = 105.0
        sleep = AsyncMock()

        def keep_one_bootstrap_task(_delay: float) -> None:
            for _ in range(5):
                manager.start_lazy_connect()
                self.assertIs(manager._lazy_connect_task, task)

        sleep.side_effect = keep_one_bootstrap_task
        with (
            patch.object(manager, "_check_availability", check_availability),
            patch.object(time, "monotonic", return_value=100.0),
            patch.object(asyncio, "sleep", sleep),
        ):
            manager.start_lazy_connect()
            task = manager._lazy_connect_task
            self.assertIsNotNone(task)
            for _ in range(5):
                manager.start_lazy_connect()
                self.assertIs(manager._lazy_connect_task, task)
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await task

        self.assertEqual(check_availability.await_count, 3)
        self.assertEqual(sleep.await_args_list, [call(5.0), call(5.0)])
        await manager.cleanup()
        mock_pool_instance.close.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_cleanup_cancels_pending_lazy_connect(
        self, mock_pool_class: Any
    ) -> None:
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.close = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)
        started = asyncio.Event()

        async def wait_forever() -> None:
            started.set()
            await asyncio.Future()

        initialize = AsyncMock(side_effect=wait_forever)

        with patch.object(manager, "initialize", initialize):
            manager.start_lazy_connect()
            await started.wait()
            await manager.cleanup()

        self.assertIsNone(manager._lazy_connect_task)
        mock_pool_instance.close.assert_awaited_once()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_detach_unusable_player_finds_registered_unavailable_node(
        self, mock_pool_class: Any
    ) -> None:
        guild = MagicMock(id=123)
        node = MagicMock(label="down", available=False)
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.label_to_node = {"down": node}
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)
        player = _FakeMusicPlayer(guild, node)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        mark_node_unavailable = AsyncMock()
        local_cleanup = AsyncMock()

        with (
            patch.object(manager, "mark_node_unavailable", mark_node_unavailable),
            patch.object(
                manager,
                "_cleanup_voice_client_locally",
                local_cleanup,
            ),
        ):
            await manager._detach_unusable_player(guild, _as_music_player(player))

        self.assertNotIn(node, mock_pool_instance.nodes)
        self.assertIs(mock_pool_instance.label_to_node["down"], node)
        mark_node_unavailable.assert_awaited_once_with(node)
        local_cleanup.assert_awaited_once_with(guild, player)

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_remove_or_close_removes_registered_unavailable_node(
        self, mock_pool_class: Any
    ) -> None:
        node = MagicMock(label="down", available=False)
        node.close = AsyncMock()
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.label_to_node = {"down": node}
        mock_pool_instance.remove_node = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)

        await manager._remove_or_close_node(node)

        mock_pool_instance.remove_node.assert_awaited_once_with(
            node, transfer_players=False
        )
        node.close.assert_not_awaited()

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_cleanup_unavailable_nodes_uses_full_label_mapping(
        self, mock_pool_class: Any
    ) -> None:
        unavailable = MagicMock(label="down", available=False)
        available = MagicMock(label="ready", available=True)
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = [available]
        mock_pool_instance.label_to_node = {
            "down": unavailable,
            "ready": available,
        }
        mock_pool_instance.remove_node = AsyncMock()
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)

        await manager._cleanup_unavailable_nodes()

        mock_pool_instance.remove_node.assert_awaited_once_with(
            unavailable, transfer_players=False
        )

    @patch("api.music.service.connection_manager.mafic.NodePool")
    async def test_mark_node_unavailable_sets_cooldown_and_removes_node(
        self, mock_pool_class: Any
    ) -> None:
        node = MagicMock(label="down", available=False)
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        registered_nodes = {"down": node}
        mock_pool_instance.label_to_node = registered_nodes
        mock_pool_instance.remove_node = AsyncMock()

        def remove_node_side_effect(*_args: Any, **_kwargs: Any) -> None:
            registered_nodes.pop("down")

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
    ) -> None:
        node = MagicMock(label="missing")
        node.close = AsyncMock()
        mock_pool_instance = MagicMock()
        mock_pool_instance.nodes = []
        mock_pool_instance.label_to_node = {}
        mock_pool_class.return_value = mock_pool_instance
        manager = ConnectionManager(self.bot)

        await manager.mark_node_unavailable(node)

        node.close.assert_awaited_once()

    async def test_node_unavailable_transfers_player_through_public_api(self) -> None:
        old_node = MagicMock(label="old", available=False)
        target = MagicMock(label="target", available=True)
        guild = MagicMock(id=123)
        player = _FakeMusicPlayer(guild, old_node)
        guild.voice_client = player
        self.bot.get_guild.return_value = guild
        mock_pool = MagicMock()
        mock_pool.nodes = [old_node, target]
        self.manager.pool = cast(Any, mock_pool)
        old_node.players = [player]

        async def transfer(actual_target: object) -> None:
            self.assertIs(actual_target, target)
            player.assigned_node = target

        player.transfer_to = AsyncMock(side_effect=transfer)
        self.manager.mark_node_unavailable = AsyncMock()
        self.manager._cleanup_voice_client_locally = AsyncMock()

        invalidated = await self.manager.handle_node_unavailable(old_node)

        self.assertEqual(invalidated, set())
        player.transfer_to.assert_awaited_once_with(target)
        self.manager.mark_node_unavailable.assert_awaited_once_with(old_node)
        self.manager._cleanup_voice_client_locally.assert_not_awaited()

    async def test_failed_node_transfer_invalidates_only_affected_player(self) -> None:
        old_node = MagicMock(label="old", available=False)
        target = MagicMock(label="target", available=True)
        affected_guild = MagicMock(id=123)
        unaffected_guild = MagicMock(id=456)
        affected = _FakeMusicPlayer(affected_guild, old_node)
        unaffected = _FakeMusicPlayer(unaffected_guild, target)
        affected_guild.voice_client = affected
        unaffected_guild.voice_client = unaffected
        guilds = {
            123: affected_guild,
            456: unaffected_guild,
        }
        self.bot.get_guild.side_effect = guilds.get
        mock_pool = MagicMock()
        mock_pool.nodes = [old_node, target]
        self.manager.pool = cast(Any, mock_pool)
        old_node.players = [affected]
        affected.transfer_to = AsyncMock(side_effect=TimeoutError)
        self.manager.mark_node_unavailable = AsyncMock()
        self.manager._cleanup_voice_client_locally = AsyncMock()

        invalidated = await self.manager.handle_node_unavailable(old_node)

        self.assertEqual(invalidated, {123})
        self.assertTrue(affected.is_stale)
        self.assertFalse(unaffected.is_stale)
        self.manager._cleanup_voice_client_locally.assert_awaited_once_with(
            affected_guild, affected
        )


class TestLavalinkBootstrap(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.pool = MagicMock()
        self.pool.nodes = []
        self.pool.label_to_node = {}
        self.pool.close = AsyncMock()
        self.pool.remove_node = AsyncMock()
        with patch.object(mafic, "NodePool", return_value=self.pool):
            self.manager = ConnectionManager(MagicMock())

    @override
    async def asyncTearDown(self) -> None:
        await self.manager.cleanup()

    async def test_ensure_available_returns_false_for_terminal_http_errors(
        self,
    ) -> None:
        for error in (
            mafic.HTTPUnauthorized("invalid credentials"),
            mafic.HTTPException(403, "forbidden"),
        ):
            with self.subTest(error=type(error).__name__):
                node = MagicMock()
                node.close = AsyncMock()
                self.pool.add_node = AsyncMock(side_effect=error)
                self.manager._initialized = True

                with patch.object(mafic, "Node", return_value=node):
                    self.assertFalse(await self.manager.ensure_available())
                    self.assertFalse(self.manager._initialized)
                    self.assertTrue(self.manager.is_known_unavailable())
                    self.assertEqual(
                        self.manager._last_connect_error, type(error).__name__
                    )
                    self.assertEqual(self.manager._next_connect_retry_at, 0.0)
                    self.assertFalse(await self.manager.ensure_available())

                self.assertEqual(self.pool.add_node.await_count, 2)
                self.assertEqual(node.close.await_count, 2)

    async def test_join_unlocked_returns_unavailable_for_terminal_http_errors(
        self,
    ) -> None:
        for error in (
            mafic.HTTPUnauthorized("invalid credentials"),
            mafic.HTTPException(403, "forbidden"),
        ):
            with self.subTest(error=type(error).__name__):
                guild = MagicMock(spec=discord.Guild, voice_client=None)
                channel = MagicMock(spec=discord.VoiceChannel)
                channel.connect = AsyncMock()
                node = MagicMock()
                node.close = AsyncMock()
                self.pool.add_node = AsyncMock(side_effect=error)

                with patch.object(mafic, "Node", return_value=node):
                    result = await self.manager._join_unlocked(guild, channel)

                self.assertEqual(
                    result, (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None)
                )
                self.pool.add_node.assert_awaited_once()
                channel.connect.assert_not_awaited()

    async def test_ensure_available_propagates_programming_errors(self) -> None:
        for error in (RuntimeError("bug"), TypeError("bug"), AssertionError("bug")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(self.manager, "initialize", side_effect=error),
                self.assertRaises(type(error)) as raised,
            ):
                await self.manager.ensure_available()

            self.assertIs(raised.exception, error)

    async def test_transient_connect_errors_become_retryable_node_unavailability(
        self,
    ) -> None:
        request_info = MagicMock(
            spec=aiohttp.RequestInfo, real_url="https://localhost:2333/v4/websocket"
        )
        errors = (
            mafic.HTTPException(503, "unavailable"),
            mafic.HTTPException(429, "rate limited"),
            aiohttp.WSServerHandshakeError(request_info, (), status=503),
            aiohttp.WSServerHandshakeError(request_info, (), status=429),
            aiohttp.ClientResponseError(request_info, (), status=503),
            aiohttp.ClientConnectionError("offline"),
            aiohttp.ClientPayloadError("incomplete response"),
            TimeoutError(),
        )
        for error in errors:
            with self.subTest(error=repr(error)):
                node = MagicMock()
                node.close = AsyncMock()
                self.pool.add_node = AsyncMock(side_effect=error)

                with (
                    patch.object(mafic, "Node", return_value=node),
                    patch.object(time, "monotonic", return_value=100.0),
                    self.assertRaises(NodeNotConnectedError) as raised,
                ):
                    await self.manager.initialize()

                self.assertIs(raised.exception.__cause__, error)
                self.assertEqual(
                    self.manager._next_connect_retry_at,
                    100.0 + config.LAVALINK_CONNECT_RETRY_DELAY,
                )
                self.pool.add_node.assert_awaited_once()
                node.close.assert_awaited_once_with()

    async def test_terminal_connect_errors_stop_bootstrap_without_retry(self) -> None:
        request_info = MagicMock(
            spec=aiohttp.RequestInfo, real_url="https://localhost:2333/v4/websocket"
        )
        connection_key = MagicMock(host="localhost", port=2333, is_ssl=True, ssl=True)
        errors = (
            mafic.HTTPUnauthorized("invalid credentials"),
            mafic.HTTPBadRequest("bad request"),
            mafic.HTTPNotFound("missing endpoint"),
            mafic.HTTPException(403, "forbidden"),
            aiohttp.WSServerHandshakeError(request_info, (), status=401),
            aiohttp.WSServerHandshakeError(request_info, (), status=403),
            aiohttp.ClientResponseError(request_info, (), status=400),
            aiohttp.InvalidURL("invalid URL"),
            aiohttp.RedirectClientError("invalid redirect"),
            aiohttp.TooManyRedirects(
                request_info, (MagicMock(spec=aiohttp.ClientResponse),), status=302
            ),
            aiohttp.ClientError("unclassified client error"),
            aiohttp.ClientConnectorCertificateError(
                connection_key, ssl.SSLCertVerificationError(1, "untrusted certificate")
            ),
            aiohttp.ClientConnectorSSLError(
                connection_key, ssl.SSLError(1, "unsupported TLS protocol")
            ),
            aiohttp.ServerFingerprintMismatch(
                b"expected", b"actual", "localhost", 2333
            ),
        )
        for error in errors:
            with self.subTest(error=repr(error)):
                node = MagicMock()
                node.close = AsyncMock()
                self.pool.add_node = AsyncMock(side_effect=error)
                sleep = AsyncMock()

                with (
                    patch.object(mafic, "Node", return_value=node),
                    patch.object(asyncio, "sleep", sleep),
                    self.assertLogs(
                        connection_module.logger, level="WARNING"
                    ) as captured,
                ):
                    self.manager.start_lazy_connect()
                    task = self.manager._lazy_connect_task
                    if task is None:
                        self.fail("Bootstrap task was not scheduled")
                    await task

                self.pool.add_node.assert_awaited_once()
                node.close.assert_awaited_once_with()
                sleep.assert_not_awaited()
                self.assertFalse(self.manager._initialized)
                self.assertTrue(self.manager.is_known_unavailable())
                self.assertEqual(self.manager._last_connect_error, type(error).__name__)
                self.assertEqual(self.manager._next_connect_retry_at, 0.0)
                self.assertEqual(len(captured.records), 1)
                self.assertEqual(
                    captured.records[0].getMessage(),
                    "Lavalink unavailable due to terminal connection failure "
                    + f"({type(error).__name__})",
                )
                self.assertIsNone(captured.records[0].exc_info)

    async def test_first_attempt_success_exits_without_sleep(self) -> None:
        initialize = AsyncMock(
            side_effect=lambda: setattr(self.pool, "nodes", [MagicMock(available=True)])
        )
        sleep = AsyncMock()

        with (
            patch.object(self.manager, "initialize", initialize),
            patch.object(asyncio, "sleep", sleep),
        ):
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await task

        initialize.assert_awaited_once_with()
        sleep.assert_not_awaited()
        self.assertTrue(task.done())

    async def test_unavailable_attempt_retries_after_remaining_cooldown(self) -> None:
        await self._assert_bootstrap_retries_after_cooldown(
            aiohttp.ClientConnectionError("offline")
        )

    async def test_http_503_retries_after_remaining_cooldown(self) -> None:
        await self._assert_bootstrap_retries_after_cooldown(
            mafic.HTTPException(503, "unavailable")
        )

    async def _assert_bootstrap_retries_after_cooldown(self, error: Exception) -> None:
        node = MagicMock(label="test", available=True)
        node.close = AsyncMock()
        attempts = 0

        def connect(_node: object, *, player_cls: object) -> None:
            nonlocal attempts
            self.assertIs(player_cls, MusicPlayer)
            attempts += 1
            if attempts == 1:
                raise error
            self.pool.nodes = [node]
            self.pool.label_to_node = {"test": node}

        self.pool.add_node = AsyncMock(side_effect=connect)
        with (
            patch.object(mafic, "Node", return_value=node),
            patch.object(time, "monotonic", return_value=100.0) as clock,
            patch.object(asyncio, "sleep", new=AsyncMock()) as sleep,
        ):

            def advance_clock(delay: float) -> None:
                self.assertFalse(self.manager._init_lock.locked())
                clock.return_value = 100.0 + delay

            sleep.side_effect = advance_clock
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await task

        self.assertEqual(attempts, 2)
        sleep.assert_awaited_once_with(config.LAVALINK_CONNECT_RETRY_DELAY)
        node.close.assert_awaited_once_with()
        self.assertTrue(self.manager.has_ready_node())
        self.assertEqual(self.manager._next_connect_retry_at, 0.0)

    async def test_ready_node_from_another_path_ends_sleep_without_initializing(
        self,
    ) -> None:
        sleeping = asyncio.Event()
        resume = asyncio.Event()
        self.manager._next_connect_retry_at = 105.0
        initialize = AsyncMock()

        async def wait_for_node(_delay: float) -> None:
            sleeping.set()
            await resume.wait()

        with (
            patch.object(time, "monotonic", return_value=100.0),
            patch.object(asyncio, "sleep", wait_for_node),
            patch.object(self.manager, "initialize", initialize),
        ):
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await sleeping.wait()
            for _ in range(5):
                self.manager.start_lazy_connect()
                self.assertIs(self.manager._lazy_connect_task, task)
            self.pool.nodes = [MagicMock(available=True)]
            resume.set()
            await task

        initialize.assert_not_awaited()

    async def test_cleanup_cancels_bootstrap_while_sleeping(self) -> None:
        sleeping = asyncio.Event()
        initialize = AsyncMock()

        async def wait_forever(_delay: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        with (
            patch.object(self.manager, "initialize", initialize),
            patch.object(asyncio, "sleep", wait_forever),
        ):
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await sleeping.wait()
            await self.manager.cleanup()

        self.assertTrue(task.cancelled())
        self.assertIsNone(self.manager._lazy_connect_task)
        initialize.assert_awaited_once_with()
        self.pool.close.assert_awaited_once_with()

    async def test_unexpected_availability_error_is_logged_once_and_stops(self) -> None:
        for error in (RuntimeError("bug"), TypeError("bug"), AssertionError("bug")):
            with self.subTest(error=type(error).__name__):
                initialize = AsyncMock(side_effect=error)
                sleep = AsyncMock()
                with (
                    patch.object(self.manager, "initialize", initialize),
                    patch.object(asyncio, "sleep", sleep),
                    self.assertLogs(
                        connection_module.logger, level="ERROR"
                    ) as captured,
                ):
                    self.manager.start_lazy_connect()
                    task = self.manager._lazy_connect_task
                    if task is None:
                        self.fail("Bootstrap task was not scheduled")
                    await task

                initialize.assert_awaited_once_with()
                sleep.assert_not_awaited()
                self.assertEqual(len(captured.records), 1)
                self.assertEqual(
                    captured.records[0].getMessage(),
                    "Unexpected lazy Lavalink connection failure",
                )
                self.assertIsNotNone(captured.records[0].exc_info)

    async def test_programming_error_in_initialize_is_not_converted_to_unavailable(
        self,
    ) -> None:
        node = MagicMock()
        node.close = AsyncMock()
        self.pool.add_node = AsyncMock(side_effect=RuntimeError("bug"))
        sleep = AsyncMock()

        with (
            patch.object(mafic, "Node", return_value=node),
            patch.object(asyncio, "sleep", sleep),
            self.assertLogs(connection_module.logger, level="ERROR") as captured,
        ):
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await task

        self.pool.add_node.assert_awaited_once()
        node.close.assert_awaited_once_with()
        sleep.assert_not_awaited()
        self.assertEqual(len(captured.records), 1)
        self.assertIsNone(self.manager._last_connect_error)

    async def test_cleanup_during_initialization_closes_unregistered_node(self) -> None:
        node = MagicMock()
        node.close = AsyncMock()
        connecting = asyncio.Event()

        async def wait_forever(_node: object, *, player_cls: object) -> None:
            self.assertIs(player_cls, MusicPlayer)
            connecting.set()
            await asyncio.Event().wait()

        self.pool.add_node = AsyncMock(side_effect=wait_forever)
        with patch.object(mafic, "Node", return_value=node):
            self.manager.start_lazy_connect()
            task = self.manager._lazy_connect_task
            if task is None:
                self.fail("Bootstrap task was not scheduled")
            await connecting.wait()
            await self.manager.cleanup()

        self.assertTrue(task.cancelled())
        self.assertIsNone(self.manager._lazy_connect_task)
        node.close.assert_awaited_once_with()
        self.pool.close.assert_awaited_once_with()
