"""Tests for transient voice websocket closes during channel moves."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import mafic

from api.music.models import ControllerDestroyReason
from api.music.service.event_handlers import MusicEventHandlers
from tests.api.music.helpers import make_track


class TestMusicEventHandlers(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.mark_node_unavailable = AsyncMock()
        self.connection.detach_stale_voice_client = AsyncMock()
        self.state = MagicMock()
        self.state.is_timer_active.return_value = False
        self.state.cancel_timer = MagicMock()
        self.ui = MagicMock()
        self.ui.controller.destroy_for_guild = AsyncMock()
        self.healer = MagicMock()
        self.healer.cleanup_after_disconnect = AsyncMock()
        self.handlers = MusicEventHandlers(
            self.bot,
            self.connection,
            self.state,
            self.ui,
            self.healer,
        )

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

        with patch("api.music.service.event_handlers.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_delayed_validation_requires_recovered_voice_channel(self) -> None:
        player = MagicMock(connected=True, channel=None, current=MagicMock())
        self.connection.get_player.return_value = player

        with patch("api.music.service.event_handlers.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT
        )

    async def test_node_unavailable_marks_connection_and_cleans_music_state(
        self,
    ) -> None:
        node = MagicMock()
        node.label = "MAIN"
        player = MagicMock(spec=object)
        player.disconnect = AsyncMock()
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = player
        self.bot.guilds = [guild]

        with patch("api.music.service.event_handlers.mafic.Player", object):
            await self.handlers.on_node_unavailable(node)

        self.connection.mark_node_unavailable.assert_awaited_once_with(node)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.PLAYER_ERROR,
        )
        self.state.cancel_timer.assert_called_once_with(123)
        self.connection.detach_stale_voice_client.assert_awaited_once_with(
            guild, player
        )

    async def test_track_end_finished_uses_end_transition(self) -> None:
        track = make_track("finished")
        player = MagicMock()
        player.guild.id = 123
        player.advance_after_end = AsyncMock()
        player.start_queued_if_idle = AsyncMock()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.FINISHED)

        await self.handlers._on_track_end(event)

        player.advance_after_end.assert_awaited_once_with(track)
        player.start_queued_if_idle.assert_not_awaited()

    async def test_track_end_load_failed_uses_end_transition(self) -> None:
        track = make_track("failed")
        player = MagicMock()
        player.guild.id = 123
        player.advance_after_end = AsyncMock()
        player.start_queued_if_idle = AsyncMock()
        event = MagicMock(
            player=player, track=track, reason=mafic.EndReason.LOAD_FAILED
        )

        with patch.object(
            self.handlers,
            "_handle_load_failure",
            new=AsyncMock(),
        ):
            await self.handlers._on_track_end(event)

        player.advance_after_end.assert_awaited_once_with(track)
        player.start_queued_if_idle.assert_not_awaited()

    async def test_track_end_stopped_starts_queued_if_idle(self) -> None:
        track = make_track("stopped")
        player = MagicMock()
        player.guild.id = 123
        player.advance_after_end = AsyncMock()
        player.start_queued_if_idle = AsyncMock()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.STOPPED)

        await self.handlers._on_track_end(event)

        player.advance_after_end.assert_not_awaited()
        player.start_queued_if_idle.assert_awaited_once_with()

    async def test_track_end_replaced_does_not_advance(self) -> None:
        track = make_track("replaced")
        player = MagicMock()
        player.guild.id = 123
        player.advance_after_end = AsyncMock()
        player.start_queued_if_idle = AsyncMock()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.REPLACED)

        await self.handlers._on_track_end(event)

        player.advance_after_end.assert_not_awaited()
        player.start_queued_if_idle.assert_not_awaited()

    async def test_repeated_setup_does_not_register_duplicate_listeners(self) -> None:
        self.handlers.setup()
        self.handlers.setup()

        listeners = [
            call.args
            for call in self.bot.add_listener.call_args_list
            if len(call.args) == 2
        ]

        self.assertEqual(
            listeners.count((self.handlers.on_node_ready, "on_node_ready")), 1
        )
        self.assertEqual(
            listeners.count((self.handlers.on_node_unavailable, "on_node_unavailable")),
            1,
        )

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

        with patch("api.music.service.event_handlers.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT
        )
