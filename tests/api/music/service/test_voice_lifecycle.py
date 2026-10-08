"""Tests for voice and node lifecycle orchestration."""

import asyncio
import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from api.music.models import ControllerDestroyReason
from api.music.service.voice_lifecycle import VoiceLifecycleHandlers


class TestVoiceLifecycleHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_drains_healing_despite_cancelled_waiter(self) -> None:
        entered = asyncio.Event()
        cancelling = asyncio.Event()
        release = asyncio.Event()

        async def heal(_guild_id: int) -> bool:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()
            return True

        self.healer.capture_and_heal = AsyncMock(side_effect=heal)
        healing = asyncio.create_task(self.handlers.heal(123))
        await entered.wait()
        cleanup = asyncio.create_task(self.handlers.cleanup())
        await cancelling.wait()
        self.assertFalse(cleanup.done())
        self.assertFalse(await self.handlers.heal(456))
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        self.assertFalse(healing.done())
        release.set()
        await self.handlers.cleanup()
        self.assertTrue(healing.cancelled())
        self.assertFalse(self.handlers.is_healing(123))
        self.healer.capture_and_heal.assert_awaited_once_with(123)

    async def test_cleanup_waits_for_superseded_validator(self) -> None:
        entered = asyncio.Event()
        cancelling = asyncio.Event()
        replacement_entered = asyncio.Event()
        replacement_cancelled = asyncio.Event()
        release = asyncio.Event()

        async def validate(_guild_id: int, _player: object) -> None:
            if not entered.is_set():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelling.set()
                    await release.wait()
            else:
                replacement_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    replacement_cancelled.set()

        with patch.object(
            self.handlers, "_validate_voice_transition_recovery", side_effect=validate
        ):
            self.handlers._schedule_voice_transition_validation(
                123, self._make_player()
            )
            await entered.wait()
            self.handlers._schedule_voice_transition_validation(
                123, self._make_player()
            )
            await cancelling.wait()
            await replacement_entered.wait()
            cleanup = asyncio.create_task(self.handlers.cleanup())
            await replacement_cancelled.wait()
            self.assertFalse(cleanup.done())
            release.set()
            await cleanup
        self.connection.invalidate_player.assert_not_awaited()

    async def test_cleanup_rejects_late_events_and_removes_listeners_once(self) -> None:
        self.handlers.setup()
        await self.handlers.cleanup()
        await self.handlers.cleanup()
        self.handlers.setup()
        await self.handlers._on_websocket_closed(
            MagicMock(player=self._make_player(), code=4006)
        )
        self.handlers._schedule_voice_transition_validation(123, self._make_player())
        self.assertFalse(self.handlers._voice_transition_validation_tasks)
        self.assertEqual(self.bot.add_listener.call_count, 4)
        self.assertEqual(self.bot.remove_listener.call_count, 4)
        self.healer.capture_and_heal.assert_not_called()
        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_new_move_supersedes_pending_failed_validation(self) -> None:
        player = self._make_player()
        self.handlers._recent_voice_transitions[123] = 1.0

        async def wait_ready(*_args: object, **_kwargs: object) -> bool:
            self.handlers._recent_voice_transitions[123] = 2.0
            return False

        self.connection.wait_voice_ready.side_effect = wait_ready
        with patch(
            "api.music.service.voice_lifecycle.asyncio.sleep", new_callable=AsyncMock
        ):
            await self.handlers._validate_voice_transition_recovery(123, player)
        self.ui.controller.destroy_for_guild.assert_not_awaited()
        self.connection.invalidate_player.assert_not_awaited()

    async def test_leave_can_cancel_healing_during_initial_controller_cleanup(
        self,
    ) -> None:
        entered = asyncio.Event()

        async def cleanup(*_args: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        self.ui.controller.destroy_for_guild.side_effect = cleanup
        task = asyncio.create_task(self.handlers.heal(123))
        await entered.wait()
        await self.handlers.cancel_heal(123)
        self.assertTrue(task.cancelled())
        self.assertFalse(self.handlers.is_healing(123))
        self.healer.capture_and_heal.assert_not_called()

    async def test_cancelled_validator_preserves_new_transition_marker(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def wait(_delay: float) -> None:
            entered.set()
            await release.wait()

        player = self._make_player()
        self.handlers._recent_voice_transitions[123] = 1.0
        with patch("api.music.service.voice_lifecycle.asyncio.sleep", wait):
            self.handlers._schedule_voice_transition_validation(123, player)
            old = self.handlers._voice_transition_validation_tasks[123]
            await entered.wait()
            self.handlers._recent_voice_transitions[123] = 2.0
            self.handlers._schedule_voice_transition_validation(123, player)
            new = self.handlers._voice_transition_validation_tasks[123]
            await asyncio.gather(old, return_exceptions=True)
            self.assertEqual(self.handlers._recent_voice_transitions[123], 2.0)
            self.assertIs(self.handlers._voice_transition_validation_tasks[123], new)
            new.cancel()
            await asyncio.gather(new, return_exceptions=True)

    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.is_current_player.return_value = True
        self.connection.is_transitioning.return_value = False
        self.connection.wait_voice_ready = AsyncMock(return_value=True)
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
        self.connection.wait_voice_ready.return_value = False
        player = MagicMock(connected=True, channel=None, current=MagicMock())
        self.connection.get_player.return_value = player

        with patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT, expected_player=player
        )

    async def test_delayed_validation_logs_unexpected_background_failure(self) -> None:
        self.connection.wait_voice_ready.return_value = False
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

    def testempty_channel_reason_for_channel_without_humans(self) -> None:
        channel = MagicMock()
        channel.members = [MagicMock(bot=True)]

        reason = self.handlers.empty_channel_reason(channel)

        self.assertEqual(reason, "empty")

    def testempty_channel_reason_for_all_deafened_humans(self) -> None:
        member = MagicMock(bot=False)
        member.voice.self_deaf = True
        member.voice.deaf = False
        channel = MagicMock()
        channel.members = [member]

        reason = self.handlers.empty_channel_reason(channel)

        self.assertEqual(reason, "all_deafened")

    def testempty_channel_reason_is_none_for_active_human(self) -> None:
        member = MagicMock(bot=False)
        member.voice.self_deaf = False
        member.voice.deaf = False
        channel = MagicMock()
        channel.members = [member]

        reason = self.handlers.empty_channel_reason(channel)

        self.assertIsNone(reason)

    async def test_delayed_transition_validation_destroys_disconnected_controller(
        self,
    ) -> None:
        self.connection.wait_voice_ready.return_value = False
        player = MagicMock(connected=False, current=None)
        self.connection.get_player.return_value = player
        self.connection.is_player_usable.return_value = False

        with patch("api.music.service.voice_lifecycle.asyncio.sleep", new=AsyncMock()):
            await self.handlers._validate_voice_transition_recovery(1, player)

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1, ControllerDestroyReason.VOICE_DISCONNECT, expected_player=player
        )
        self.connection.invalidate_player.assert_awaited_once_with(
            player,
            context="voice_transition_validation",
        )
