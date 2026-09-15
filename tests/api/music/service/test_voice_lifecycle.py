"""Tests for voice and node lifecycle orchestration."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from api.music.models import ControllerDestroyReason
from api.music.service.voice_lifecycle import VoiceLifecycleHandlers


class TestVoiceLifecycleHandlers(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.is_current_player.return_value = True
        self.connection.handle_node_unavailable = AsyncMock(return_value=set())
        self.connection.mark_node_unavailable = AsyncMock()
        self.connection.detach_stale_voice_client = AsyncMock()
        self.connection.invalidate_player = AsyncMock()
        self.connection.is_player_usable.return_value = True
        self.state = MagicMock()
        self.state.is_timer_active.return_value = False
        self.state.cancel_timer = MagicMock()
        self.ui = MagicMock()
        self.ui.spawn_controller = AsyncMock()
        self.ui.controller.destroy_for_guild = AsyncMock()
        self.healer = MagicMock()
        self.handlers = VoiceLifecycleHandlers(
            self.bot,
            self.connection,
            self.state,
            self.ui,
            self.healer,
        )

    def _make_player(self) -> MagicMock:
        player = MagicMock()
        player.guild.id = 123
        return player

    async def test_non_current_websocket_close_has_no_side_effects(self) -> None:
        player = self._make_player()
        event = MagicMock(
            player=player,
            code=4006,
            reason="stale websocket",
            by_discord=False,
        )
        self.connection.is_current_player.return_value = False

        with (
            patch.object(self.handlers, "heal", new=AsyncMock()) as heal,
            patch.object(
                self.handlers, "_schedule_voice_transition_validation"
            ) as schedule,
        ):
            await self.handlers._on_websocket_closed(event)

        heal.assert_not_awaited()
        schedule.assert_not_called()
        self.connection.detach_stale_voice_client.assert_not_awaited()

    async def test_websocket_close_after_recent_move_defers_controller_cleanup(
        self,
    ) -> None:
        self.bot.user.id = 99
        member = MagicMock()
        member.id = 99
        member.guild.id = 1
        before = MagicMock()
        before.channel = MagicMock(name="old-channel")
        before.channel.name = "old"
        after = MagicMock()
        after.channel = MagicMock(name="new-channel")
        after.channel.name = "new"
        event = MagicMock()
        event.code = 4022
        event.reason = "Disconnected: Call terminated"
        event.by_discord = False
        event.player.guild.id = 1

        await self.handlers._handle_bot_voice_state_update(member, before, after)
        self.healer.capture_and_heal.assert_not_called()

        with patch.object(
            self.handlers, "_schedule_voice_transition_validation"
        ) as schedule:
            await self.handlers._on_websocket_closed(event)

        self.ui.controller.destroy_for_guild.assert_not_awaited()
        schedule.assert_called_once_with(1, event.player)

    async def test_code_1000_after_recent_move_defers_controller_cleanup(self) -> None:
        self.bot.user.id = 99
        member = MagicMock()
        member.id = 99
        member.guild.id = 1
        before = MagicMock()
        before.channel = MagicMock(name="old-channel")
        before.channel.name = "old"
        after = MagicMock()
        after.channel = MagicMock(name="new-channel")
        after.channel.name = "new"
        event = MagicMock()
        event.code = 1000
        event.reason = ""
        event.by_discord = False
        event.player.guild.id = 1

        await self.handlers._handle_bot_voice_state_update(member, before, after)

        with patch.object(
            self.handlers, "_schedule_voice_transition_validation"
        ) as schedule:
            await self.handlers._on_websocket_closed(event)

        self.ui.controller.destroy_for_guild.assert_not_awaited()
        schedule.assert_called_once_with(1, event.player)

    async def test_repeated_websocket_close_after_move_does_not_destroy_controller(
        self,
    ) -> None:
        self.bot.user.id = 99
        member = MagicMock()
        member.id = 99
        member.guild.id = 1
        before = MagicMock()
        before.channel = MagicMock(name="old-channel")
        before.channel.name = "old"
        after = MagicMock()
        after.channel = MagicMock(name="new-channel")
        after.channel.name = "new"
        event = MagicMock()
        event.code = 4022
        event.reason = "Disconnected: Call terminated"
        event.by_discord = False
        event.player.guild.id = 1

        await self.handlers._handle_bot_voice_state_update(member, before, after)

        with patch.object(
            self.handlers, "_schedule_voice_transition_validation"
        ) as schedule:
            await self.handlers._on_websocket_closed(event)
            await self.handlers._on_websocket_closed(event)

        self.ui.controller.destroy_for_guild.assert_not_awaited()
        self.assertEqual(schedule.call_count, 2)

    async def test_delayed_transition_validation_preserves_recovered_controller(
        self,
    ) -> None:
        player = MagicMock(connected=True, channel=MagicMock(), current=MagicMock())
        self.connection.get_player.return_value = player

        with patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_delayed_validation_requires_recovered_voice_channel(self) -> None:
        player = MagicMock(connected=True, channel=None, current=MagicMock())
        self.connection.get_player.return_value = player

        with patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT
        )

    async def test_delayed_validation_logs_unexpected_background_failure(self) -> None:
        player = MagicMock(connected=False, current=None)
        self.connection.get_player.return_value = player
        self.ui.controller.destroy_for_guild.side_effect = RuntimeError(
            "programming failure"
        )

        with (
            patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()),
            self.assertLogs(
                "api.music.service.voice_lifecycle",
                level="ERROR",
            ),
        ):
            await self.handlers._validate_voice_transition_recovery(1, player)

    async def test_node_unavailable_marks_connection_and_cleans_music_state(
        self,
    ) -> None:
        node = MagicMock()
        node.label = "MAIN"
        node.players = []
        player = MagicMock()
        player.disconnect = AsyncMock()
        player.guild.id = 123
        node.players = [player]
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = player
        self.bot.guilds = [guild]

        self.connection.handle_node_unavailable.return_value = {123}
        with patch("api.music.service.voice_lifecycle.MusicPlayer", object):
            await self.handlers.on_node_unavailable(node)

        self.connection.handle_node_unavailable.assert_awaited_once_with(node)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.PLAYER_ERROR,
        )
        self.state.cancel_timer.assert_called_once_with(123)
        self.connection.detach_stale_voice_client.assert_not_awaited()
        self.healer.capture_and_heal.assert_not_called()

    async def test_node_unavailable_only_cleans_guilds_on_affected_node(self) -> None:
        node_a = MagicMock(label="A")
        node_b = MagicMock(label="B")
        player_a = MagicMock(is_stale=False, assigned_node=node_a)
        player_a.guild.id = 1
        player_b = MagicMock(is_stale=False, assigned_node=node_b)
        player_b.guild.id = 2
        node_a.players = [player_a]
        node_b.players = [player_b]
        self.connection.handle_node_unavailable.return_value = {1}

        with patch("api.music.service.voice_lifecycle.MusicPlayer", object):
            await self.handlers.on_node_unavailable(node_a)

        self.connection.handle_node_unavailable.assert_awaited_once_with(node_a)
        self.assertFalse(player_b.is_stale)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1,
            ControllerDestroyReason.PLAYER_ERROR,
        )
        self.state.cancel_timer.assert_called_once_with(1)

    def test_empty_channel_reason_for_channel_without_humans(self) -> None:
        channel = MagicMock()
        channel.members = [MagicMock(bot=True)]

        reason = self.handlers._empty_channel_reason(channel)

        self.assertEqual(reason, "empty")

    def test_empty_channel_reason_for_all_deafened_humans(self) -> None:
        member = MagicMock(bot=False)
        member.voice.self_deaf = True
        member.voice.deaf = False
        channel = MagicMock()
        channel.members = [member]

        reason = self.handlers._empty_channel_reason(channel)

        self.assertEqual(reason, "all_deafened")

    def test_empty_channel_reason_is_none_for_active_human(self) -> None:
        member = MagicMock(bot=False)
        member.voice.self_deaf = False
        member.voice.deaf = False
        channel = MagicMock()
        channel.members = [member]

        reason = self.handlers._empty_channel_reason(channel)

        self.assertIsNone(reason)

    async def test_delayed_transition_validation_destroys_disconnected_controller(
        self,
    ) -> None:
        player = MagicMock(connected=False, current=None)
        self.connection.get_player.return_value = player
        self.connection.is_player_usable.return_value = False

        with patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT
        )
        self.connection.invalidate_player.assert_awaited_once_with(
            player,
            context="voice_transition_validation",
        )
