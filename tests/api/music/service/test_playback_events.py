"""Tests for token-correlated playback event orchestration."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import mafic

from api.music.models import (
    PLAYBACK_USER_DATA_KEY,
    ControllerDestroyReason,
    PlaybackAttempt,
    QueueEntry,
    TrackEndOutcome,
    TrackExceptionPayload,
    TrackRequester,
)
from api.music.service.playback_events import PlaybackEventHandlers
from tests.api.music.helpers import make_track


def _attempt(attempt_id: int, identifier: str, token: str) -> PlaybackAttempt:
    return PlaybackAttempt(
        attempt_id,
        QueueEntry(
            attempt_id,
            make_track(identifier),
            TrackRequester(user_id=456, channel_id=789),
        ),
        event_token=token,
    )


def _event_track(identifier: str, token: str) -> mafic.Track:
    track = make_track(identifier)
    track.user_data[PLAYBACK_USER_DATA_KEY] = token
    return track


class TestPlaybackEventHandlers(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.is_current_player.return_value = True
        self.connection.invalidate_player = AsyncMock()
        self.state = MagicMock()
        self.ui = MagicMock()
        self.ui.spawn_controller = AsyncMock()
        self.ui.controller.destroy_for_guild = AsyncMock()
        self.handlers = PlaybackEventHandlers(
            self.bot,
            self.connection,
            self.state,
            self.ui,
            lambda _guild_id: False,
        )

    def _player(self) -> MagicMock:
        player = MagicMock()
        player.guild.id = 123
        player.resolve_track_start = AsyncMock()
        player.claim_track_exception = AsyncMock()
        player.resolve_exception_attempt = AsyncMock()
        player.handle_track_end = AsyncMock()
        return player

    async def test_tagged_track_start_records_state_before_spawning_ui(self) -> None:
        player = self._player()
        attempt = _attempt(1, "same", "attempt-a")
        player.resolve_track_start.return_value = attempt
        calls: list[str] = []

        def record_state(*_args: object) -> None:
            calls.append("state")

        def spawn_ui(*_args: object) -> None:
            calls.append("ui")

        self.state.record_track_start.side_effect = record_state
        self.ui.spawn_controller.side_effect = spawn_ui
        event = MagicMock(player=player, track=_event_track("same", "attempt-a"))

        await self.handlers._on_track_start(event)

        player.resolve_track_start.assert_awaited_once_with("attempt-a")
        self.assertEqual(calls, ["state", "ui"])

    async def test_same_source_track_end_tokens_select_different_attempts(self) -> None:
        player = self._player()
        first = _attempt(1, "same", "attempt-a")
        second = _attempt(2, "same", "attempt-b")
        player.handle_track_end.side_effect = (
            TrackEndOutcome(first, None, False),
            TrackEndOutcome(second, None, False),
        )

        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=_event_track("same", "attempt-a"),
                reason=mafic.EndReason.REPLACED,
            )
        )
        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=_event_track("same", "attempt-b"),
                reason=mafic.EndReason.FINISHED,
            )
        )

        self.assertEqual(
            [call.args[0] for call in player.handle_track_end.await_args_list],
            ["attempt-a", "attempt-b"],
        )
        self.assertEqual(
            [call.args[1] for call in self.state.record_history.call_args_list],
            [first, second],
        )

    async def test_stale_track_end_has_no_application_side_effects(self) -> None:
        player = self._player()
        player.handle_track_end.return_value = TrackEndOutcome(None, None, True)
        event = MagicMock(
            player=player,
            track=_event_track("same", "stale"),
            reason=mafic.EndReason.FINISHED,
        )

        await self.handlers._on_track_end(event)

        self.state.record_history.assert_not_called()
        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_track_end_transition_precedes_history_and_ui(self) -> None:
        player = self._player()
        attempt = _attempt(1, "track", "attempt-a")
        calls: list[str] = []

        async def transition(*_args: object) -> TrackEndOutcome:
            self.state.record_history.assert_not_called()
            self.ui.controller.destroy_for_guild.assert_not_awaited()
            calls.append("transition")
            return TrackEndOutcome(attempt, None, False)

        player.handle_track_end.side_effect = transition

        def record_history(*_args: object) -> None:
            calls.append("history")

        def destroy_ui(*_args: object, **_kwargs: object) -> None:
            calls.append("ui")

        self.state.record_history.side_effect = record_history
        self.ui.controller.destroy_for_guild.side_effect = destroy_ui

        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=_event_track("track", "attempt-a"),
                reason=mafic.EndReason.FINISHED,
            )
        )

        self.assertEqual(calls, ["transition", "history", "ui"])

    async def test_exception_and_stuck_use_their_event_tokens(self) -> None:
        player = self._player()
        exception_attempt = _attempt(1, "same", "attempt-a")
        stuck_attempt = _attempt(2, "same", "attempt-b")
        player.claim_track_exception.return_value = exception_attempt
        player.resolve_exception_attempt.return_value = stuck_attempt
        exception = MagicMock(
            player=player,
            track=_event_track("same", "attempt-a"),
            exception={"message": "failed", "severity": "common"},
        )
        stuck = MagicMock(
            player=player,
            track=_event_track("same", "attempt-b"),
            threshold_ms=10_000,
        )

        await self.handlers._on_track_exception(exception)
        await self.handlers._on_track_stuck(stuck)

        player.claim_track_exception.assert_awaited_once_with("attempt-a")
        player.resolve_exception_attempt.assert_awaited_once_with("attempt-b")
        self.ui.controller.destroy_for_guild.assert_any_await(
            123,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=1,
        )
        self.ui.controller.destroy_for_guild.assert_any_await(
            123,
            ControllerDestroyReason.TRACK_STUCK,
            expected_attempt_id=2,
        )

    async def test_untagged_event_is_rejected_without_fallback(self) -> None:
        player = self._player()
        event = MagicMock(
            player=player,
            track=make_track("untagged"),
            reason=mafic.EndReason.FINISHED,
        )

        with self.assertLogs(
            "api.music.service.playback_events", level="WARNING"
        ) as logs:
            await self.handlers._on_track_end(event)

        player.handle_track_end.assert_not_awaited()
        self.assertIn("Ignoring untagged TrackEndEvent", logs.output[0])

    async def test_non_current_player_event_is_rejected_before_correlation(
        self,
    ) -> None:
        player = self._player()
        self.connection.is_current_player.return_value = False
        event = MagicMock(
            player=player,
            track=_event_track("track", "attempt-a"),
            reason=mafic.EndReason.FINISHED,
        )

        await self.handlers._on_track_end(event)

        player.handle_track_end.assert_not_awaited()
        self.state.record_history.assert_not_called()

    async def test_duplicate_track_exception_dispatches_once(self) -> None:
        player = self._player()
        attempt = _attempt(1, "same", "attempt-a")
        player.claim_track_exception.side_effect = (attempt, None)
        event = MagicMock(
            player=player,
            track=_event_track("same", "attempt-a"),
            exception={"message": "failed", "severity": "common"},
        )

        with self.assertLogs(
            "api.music.service.playback_events", level="WARNING"
        ) as logs:
            await self.handlers._on_track_exception(event)
            await self.handlers._on_track_exception(event)

        self.assertEqual(len(logs.records), 1)
        self.bot.dispatch.assert_called_once()
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=attempt.attempt_id,
        )

    async def test_exception_then_load_failed_end_notifies_once(self) -> None:
        player = self._player()
        attempt = _attempt(1, "failed", "attempt-a")
        player.claim_track_exception.return_value = attempt
        player.handle_track_end.return_value = TrackEndOutcome(attempt, None, False)
        track = _event_track("failed", "attempt-a")

        await self.handlers._on_track_exception(
            MagicMock(
                player=player,
                track=track,
                exception={"message": "failed", "severity": "common"},
            )
        )
        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=track,
                reason=mafic.EndReason.LOAD_FAILED,
            )
        )

        self.bot.dispatch.assert_called_once()
        self.assertEqual(
            [
                call.args[1]
                for call in self.ui.controller.destroy_for_guild.await_args_list
            ],
            [
                ControllerDestroyReason.TRACK_EXCEPTION,
                ControllerDestroyReason.TRACK_END,
            ],
        )

    async def test_load_failed_end_dispatches_fallback_notification(self) -> None:
        player = self._player()
        attempt = _attempt(1, "failed", "attempt-a")
        player.handle_track_end.return_value = TrackEndOutcome(attempt, None, False)

        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=_event_track("failed", "attempt-a"),
                reason=mafic.EndReason.LOAD_FAILED,
            )
        )

        self.bot.dispatch.assert_called_once()
        event_name, payload = self.bot.dispatch.call_args.args
        self.assertEqual(event_name, "music_track_exception")
        self.assertIsInstance(payload, TrackExceptionPayload)
        self.assertEqual(payload.track.identifier, "failed")
        self.assertEqual(payload.requester_id, 456)

    async def test_track_end_io_failure_invalidates_before_side_effects(self) -> None:
        player = self._player()
        player.handle_track_end.side_effect = aiohttp.ClientConnectionError("down")

        await self.handlers._on_track_end(
            MagicMock(
                player=player,
                track=_event_track("failed", "attempt-a"),
                reason=mafic.EndReason.FINISHED,
            )
        )

        self.connection.invalidate_player.assert_awaited_once_with(
            player,
            context="track_end_transition_finished",
            error="ClientConnectionError",
        )
        self.state.record_history.assert_not_called()
        self.ui.controller.destroy_for_guild.assert_not_awaited()

    async def test_late_exception_preserves_new_attempt_controller(self) -> None:
        old = PlaybackAttempt(
            1,
            QueueEntry(1, make_track("same"), TrackRequester(10, 100)),
            "attempt-old",
        )
        new = PlaybackAttempt(
            2,
            QueueEntry(2, make_track("same"), TrackRequester(20, 200)),
            "attempt-new",
        )
        player = self._player()
        player.current_attempt = new
        player.claim_track_exception.return_value = old

        await self.handlers._on_track_exception(
            MagicMock(
                player=player,
                track=_event_track("same", "attempt-old"),
                exception={"message": "late failure", "severity": "common"},
            )
        )

        _, payload = self.bot.dispatch.call_args.args
        self.assertEqual(payload.requester_id, 10)
        self.assertEqual(payload.channel_id, 100)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=old.attempt_id,
        )

    async def test_cleanup_clears_failure_deduplication_state(self) -> None:
        player = self._player()
        attempt = _attempt(1, "failed", "attempt-a")
        player.claim_track_exception.return_value = attempt
        event = MagicMock(
            player=player,
            track=_event_track("failed", "attempt-a"),
            exception={"message": "failed", "severity": "common"},
        )
        self.handlers.setup()

        await self.handlers._on_track_exception(event)
        self.assertIn(123, self.handlers._load_failures)

        self.handlers.cleanup()

        self.assertEqual(self.handlers._load_failures, {})
        self.assertEqual(self.bot.remove_listener.call_count, 4)
