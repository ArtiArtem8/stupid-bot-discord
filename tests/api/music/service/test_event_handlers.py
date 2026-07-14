"""Tests for transient voice websocket closes during channel moves."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic

from api.music.models import (
    ControllerDestroyReason,
    PlaybackAttempt,
    QueueEntry,
    TrackEndOutcome,
    TrackExceptionPayload,
    TrackRequester,
)
from api.music.service.event_handlers import MusicEventHandlers
from tests.api.music.helpers import make_track


class TestMusicEventHandlers(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.is_current_player.return_value = True
        self.connection.mark_node_unavailable = AsyncMock()
        self.connection.invalidate_node_and_players = AsyncMock()
        self.connection.detach_stale_voice_client = AsyncMock()
        self.connection.invalidate_player = AsyncMock()
        self.state = MagicMock()
        self.state.is_timer_active.return_value = False
        self.state.cancel_timer = MagicMock()
        self.ui = MagicMock()
        self.ui.spawn_controller = AsyncMock()
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

    def _make_player(self) -> MagicMock:
        player = MagicMock()
        player.guild.id = 123
        attempts: dict[int, PlaybackAttempt] = {}
        claimed: set[int] = set()

        def resolve(track: mafic.Track) -> PlaybackAttempt:
            key = id(track)
            if key not in attempts:
                attempt_id = len(attempts) + 1
                attempts[key] = PlaybackAttempt(
                    attempt_id,
                    QueueEntry(
                        attempt_id,
                        track,
                        TrackRequester(user_id=456, channel_id=789),
                    ),
                )
            return attempts[key]

        async def claim(track: mafic.Track) -> PlaybackAttempt | None:
            attempt = resolve(track)
            if attempt.attempt_id in claimed:
                return None
            claimed.add(attempt.attempt_id)
            return attempt

        async def handle_end(
            track: mafic.Track, _reason: mafic.EndReason
        ) -> TrackEndOutcome:
            return TrackEndOutcome(resolve(track), None, False)

        player.resolve_current_attempt.side_effect = resolve

        async def resolve_event(track: mafic.Track) -> PlaybackAttempt:
            return resolve(track)

        player.resolve_exception_attempt = AsyncMock(side_effect=resolve_event)
        player.claim_track_exception = AsyncMock(side_effect=claim)
        player.handle_track_end = AsyncMock(side_effect=handle_end)
        return player

    async def _handle_track_exception(
        self,
        player: MagicMock,
        track: mafic.Track,
        *,
        message: str = "load failed",
    ) -> None:
        event = MagicMock()
        event.player = player
        event.track = track
        event.exception = {"message": message, "severity": "COMMON"}

        await self.handlers._on_track_exception(event)

    async def _handle_load_failed_end(
        self,
        player: MagicMock,
        track: mafic.Track,
    ) -> None:
        event = MagicMock(
            player=player,
            track=track,
            reason=mafic.EndReason.LOAD_FAILED,
        )

        await self.handlers._on_track_end(event)

    async def test_non_current_track_start_has_no_side_effects(self) -> None:
        player = self._make_player()
        track = make_track("stale-start")
        active_failure = {99}
        self.handlers._load_failures[123] = active_failure
        self.connection.is_current_player.return_value = False
        event = MagicMock(player=player, track=track)

        await self.handlers._on_track_start(event)

        self.assertEqual(self.handlers._load_failures[123], active_failure)
        self.state.record_track_start.assert_not_called()
        self.ui.spawn_controller.assert_not_awaited()

    async def test_non_current_track_end_has_no_side_effects(self) -> None:
        player = self._make_player()
        track = make_track("stale-end")
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.FINISHED)
        self.connection.is_current_player.return_value = False

        await self.handlers._on_track_end(event)

        self.state.record_history.assert_not_called()
        self.ui.controller.destroy_for_guild.assert_not_awaited()
        player.handle_track_end.assert_not_awaited()
        self.connection.invalidate_player.assert_not_awaited()

    async def test_non_current_track_exception_has_no_side_effects(self) -> None:
        player = self._make_player()
        track = make_track("stale-exception")
        active_failure = {99}
        self.handlers._load_failures[123] = active_failure
        self.connection.is_current_player.return_value = False
        event = MagicMock(
            player=player,
            track=track,
            exception={"message": "stale failure", "severity": "COMMON"},
        )

        await self.handlers._on_track_exception(event)

        self.bot.dispatch.assert_not_called()
        self.assertEqual(self.handlers._load_failures[123], active_failure)
        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_non_current_track_stuck_has_no_side_effects(self) -> None:
        player = self._make_player()
        event = MagicMock(player=player, track=make_track("stale-stuck"))
        self.connection.is_current_player.return_value = False

        await self.handlers._on_track_stuck(event)

        self.ui.controller.destroy_for_guild.assert_not_awaited()

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
        node.players = []
        player = MagicMock()
        player.disconnect = AsyncMock()
        player.guild.id = 123
        node.players = [player]
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = player
        self.bot.guilds = [guild]

        with patch("api.music.service.event_handlers.MusicPlayer", object):
            await self.handlers.on_node_unavailable(node)

        self.connection.invalidate_node_and_players.assert_awaited_once_with(player)
        self.connection.mark_node_unavailable.assert_not_awaited()
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.PLAYER_ERROR,
        )
        self.state.cancel_timer.assert_called_once_with(123)
        self.connection.detach_stale_voice_client.assert_not_awaited()

    async def test_node_unavailable_only_cleans_guilds_on_affected_node(self) -> None:
        node_a = MagicMock(label="A")
        node_b = MagicMock(label="B")
        player_a = MagicMock(is_stale=False, _node=node_a)
        player_a.guild.id = 1
        player_b = MagicMock(is_stale=False, _node=node_b)
        player_b.guild.id = 2
        node_a.players = [player_a]
        node_b.players = [player_b]
        failure_a = {1}
        failure_b = {2}
        self.handlers._load_failures = {1: failure_a, 2: failure_b}

        async def invalidate_affected_player(player: object) -> None:
            self.assertIs(player, player_a)
            player_a.is_stale = True

        self.connection.invalidate_node_and_players.side_effect = (
            invalidate_affected_player
        )

        with patch("api.music.service.event_handlers.MusicPlayer", object):
            await self.handlers.on_node_unavailable(node_a)

        self.connection.invalidate_node_and_players.assert_awaited_once_with(player_a)
        self.assertTrue(player_a.is_stale)
        self.assertFalse(player_b.is_stale)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            1,
            ControllerDestroyReason.PLAYER_ERROR,
        )
        self.state.cancel_timer.assert_called_once_with(1)
        self.assertNotIn(1, self.handlers._load_failures)
        self.assertEqual(self.handlers._load_failures[2], failure_b)

    async def test_duplicate_track_exception_dispatches_once_for_active_failure(
        self,
    ) -> None:
        player = self._make_player()
        track = make_track("same-failure")

        await self._handle_track_exception(player, track)
        await self._handle_track_exception(player, track)

        self.assertEqual(self.bot.dispatch.call_count, 1)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=1,
        )

    async def test_track_exception_then_load_failed_end_dispatches_once(self) -> None:
        player = self._make_player()
        track = make_track("exception-before-end")

        await self._handle_track_exception(player, track)
        await self._handle_load_failed_end(player, track)

        self.assertEqual(self.bot.dispatch.call_count, 1)
        player.handle_track_end.assert_awaited_once_with(
            track, mafic.EndReason.LOAD_FAILED
        )
        destroy_reasons = [
            call.args[1]
            for call in self.ui.controller.destroy_for_guild.await_args_list
        ]
        self.assertEqual(
            destroy_reasons,
            [
                ControllerDestroyReason.TRACK_EXCEPTION,
                ControllerDestroyReason.TRACK_END,
            ],
        )

    async def test_load_failed_end_without_exception_dispatches_fallback(self) -> None:
        player = self._make_player()
        track = make_track("end-fallback")

        await self._handle_load_failed_end(player, track)

        self.bot.dispatch.assert_called_once()
        event_name, payload = self.bot.dispatch.call_args.args
        self.assertEqual(event_name, "music_track_exception")
        self.assertIsInstance(payload, TrackExceptionPayload)
        self.assertEqual(payload.guild_id, 123)
        self.assertEqual(payload.track.identifier, "end-fallback")
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_END,
            expected_attempt_id=1,
        )

    async def test_interleaved_next_failure_same_identifier_dispatches_again(
        self,
    ) -> None:
        player = self._make_player()
        first = make_track("interleaved")
        second = make_track("interleaved")
        first_attempt = player.resolve_current_attempt(first)

        async def handle_end_with_next_exception(
            _track: mafic.Track, _reason: mafic.EndReason
        ) -> TrackEndOutcome:
            await self._handle_track_exception(player, second, message="second")
            return TrackEndOutcome(
                first_attempt,
                player.resolve_current_attempt(second),
                False,
            )

        player.handle_track_end.side_effect = handle_end_with_next_exception

        await self._handle_track_exception(player, first, message="first")
        await self._handle_load_failed_end(player, first)

        self.assertEqual(self.bot.dispatch.call_count, 2)
        destroy_reasons = [
            call.args[1]
            for call in self.ui.controller.destroy_for_guild.await_args_list
        ]
        self.assertEqual(
            destroy_reasons,
            [
                ControllerDestroyReason.TRACK_EXCEPTION,
                ControllerDestroyReason.TRACK_EXCEPTION,
                ControllerDestroyReason.TRACK_END,
            ],
        )

    async def test_late_exception_uses_old_attempt_and_preserves_new_controller(
        self,
    ) -> None:
        track = make_track("same")
        old = PlaybackAttempt(1, QueueEntry(1, track, TrackRequester(10, 100)))
        new = PlaybackAttempt(
            2, QueueEntry(2, make_track("same"), TrackRequester(20, 200))
        )
        player = MagicMock()
        player.guild.id = 123
        player.current_attempt = new
        player.claim_track_exception = AsyncMock(return_value=old)

        await self._handle_track_exception(player, track)

        _, payload = self.bot.dispatch.call_args.args
        self.assertEqual(payload.requester_id, 10)
        self.assertEqual(payload.channel_id, 100)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=old.attempt_id,
        )

    async def test_repeated_failed_attempts_without_track_start_dispatch_each(
        self,
    ) -> None:
        player = self._make_player()
        first = make_track("same-identifier")
        second = make_track("same-identifier")

        await self._handle_track_exception(player, first, message="first")
        await self._handle_load_failed_end(player, first)
        await self._handle_track_exception(player, second, message="second")
        await self._handle_load_failed_end(player, second)

        self.assertEqual(self.bot.dispatch.call_count, 2)
        track_exception_cleanups = [
            call
            for call in self.ui.controller.destroy_for_guild.await_args_list
            if call.args[1] is ControllerDestroyReason.TRACK_EXCEPTION
        ]
        self.assertEqual(len(track_exception_cleanups), 2)

    async def test_cleanup_clears_active_failure_state(self) -> None:
        player = self._make_player()
        track = make_track("cleanup-retry")
        self.handlers.setup()

        await self._handle_track_exception(player, track)
        await self._handle_track_exception(player, track)
        self.assertEqual(self.bot.dispatch.call_count, 1)

        self.handlers.cleanup()
        await self._handle_track_exception(player, make_track("cleanup-retry"))

        self.assertEqual(self.bot.dispatch.call_count, 2)

    async def test_track_end_finished_uses_end_transition(self) -> None:
        track = make_track("finished")
        player = self._make_player()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.FINISHED)

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_awaited_once_with(
            track, mafic.EndReason.FINISHED
        )

    async def test_track_end_transition_io_failure_invalidates_player(self) -> None:
        track = make_track("transition-failure")
        player = self._make_player()
        player.handle_track_end.side_effect = aiohttp.ClientConnectionError("down")
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.FINISHED)

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_awaited_once_with(
            track, mafic.EndReason.FINISHED
        )
        self.connection.invalidate_player.assert_awaited_once_with(player)
        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_track_end_load_failed_uses_end_transition(self) -> None:
        track = make_track("failed")
        player = self._make_player()
        event = MagicMock(
            player=player, track=track, reason=mafic.EndReason.LOAD_FAILED
        )

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_awaited_once_with(
            track, mafic.EndReason.LOAD_FAILED
        )

    async def test_load_failed_controller_is_replaced_by_next_track_start(
        self,
    ) -> None:
        failed = make_track("failed-controller")
        next_track = make_track("next-controller")
        player = self._make_player()

        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=failed,
                reason=mafic.EndReason.LOAD_FAILED,
            )
        )
        await self.handlers._on_track_start(MagicMock(player=player, track=next_track))

        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_END,
            expected_attempt_id=1,
        )
        player.handle_track_end.assert_awaited_once_with(
            failed, mafic.EndReason.LOAD_FAILED
        )
        next_attempt = player.resolve_current_attempt(next_track)
        self.ui.spawn_controller.assert_awaited_once_with(player, next_attempt)

    async def test_track_end_stopped_starts_queued_if_idle(self) -> None:
        track = make_track("stopped")
        player = self._make_player()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.STOPPED)

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_awaited_once_with(track, mafic.EndReason.STOPPED)

    async def test_track_end_replaced_does_not_advance(self) -> None:
        track = make_track("replaced")
        player = self._make_player()
        event = MagicMock(player=player, track=track, reason=mafic.EndReason.REPLACED)

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_awaited_once_with(
            track, mafic.EndReason.REPLACED
        )

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
