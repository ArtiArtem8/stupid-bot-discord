"""Tests for queue-entry and playback-attempt transitions."""

import asyncio
import unittest
from collections import deque
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic
from discord.types.voice import VoiceServerUpdate as VoiceServerUpdatePayload

from api.music.models import PlaybackAttempt, QueueEntry, RepeatMode, TrackRequester
from api.music.player import MusicPlayer
from api.music.queue import QueueManager, RepeatManager
from tests.api.music.helpers import make_entry, make_track


def _make_player(*, current: QueueEntry | None = None) -> MusicPlayer:
    player = MusicPlayer.__new__(MusicPlayer)
    player.queue = QueueManager()
    player.repeat = RepeatManager(RepeatMode.OFF)
    player._next_entry_id = (current.entry_id + 1) if current else 1
    player._next_attempt_id = 2 if current else 1
    player._current_attempt = PlaybackAttempt(1, current) if current else None
    player._pending_end_attempts = deque()
    player._exception_attempt_ids = set()
    player._transition_lock = asyncio.Lock()
    player._is_stale = False
    player._current = current.track if current else None
    player.guild = MagicMock(id=123)
    return player


def _tracks(player: MusicPlayer) -> list[mafic.Track]:
    return [entry.track for entry in player.queue]


def _require_attempt(attempt: PlaybackAttempt | None) -> PlaybackAttempt:
    if attempt is None:
        raise AssertionError("expected playback attempt")
    return attempt


def _require_entry(entry: QueueEntry | None) -> QueueEntry:
    if entry is None:
        raise AssertionError("expected queue entry")
    return entry


def _require_requester(entry: QueueEntry) -> TrackRequester:
    if entry.requester is None:
        raise AssertionError("expected requester")
    return entry.requester


class TestMusicPlayer(unittest.IsolatedAsyncioTestCase):
    def test_new_player_starts_not_stale_and_can_be_marked_stale(self) -> None:
        with patch.object(mafic.Player, "__init__", return_value=None):
            player = MusicPlayer(MagicMock(), MagicMock())
        self.assertFalse(player.is_stale)
        player.mark_stale()
        self.assertTrue(player.is_stale)

    async def test_enqueue_tracks_with_next_adds_single_track_to_front(self) -> None:
        current = make_entry("current")
        existing = make_entry("existing", entry_id=2)
        track = make_track("next")
        player = _make_player(current=current)
        player.queue.append(existing)
        await player.enqueue_tracks((track,), None, placement="next")
        self.assertEqual(_tracks(player), [track, existing.track])

    async def test_enqueue_tracks_with_next_preserves_playlist_order_at_front(
        self,
    ) -> None:
        player = _make_player(current=make_entry("current"))
        existing = make_entry("existing", entry_id=9)
        player.queue.append(existing)
        tracks = [make_track("one"), make_track("two"), make_track("three")]
        await player.enqueue_tracks(tracks, None, placement="next")
        self.assertEqual(_tracks(player), [*tracks, existing.track])

    async def test_enqueue_tracks_with_end_adds_playlist_to_back(self) -> None:
        player = _make_player(current=make_entry("current"))
        existing = make_entry("existing", entry_id=9)
        player.queue.append(existing)
        tracks = [make_track("one"), make_track("two")]
        await player.enqueue_tracks(tracks, None, placement="end")
        self.assertEqual(_tracks(player), [existing.track, *tracks])

    async def test_enqueue_tracks_with_empty_sequence_returns_none(self) -> None:
        player = _make_player(current=make_entry("current"))
        self.assertIsNone(await player.enqueue_tracks((), None, placement="end"))
        self.assertTrue(player.queue.is_empty)

    async def test_enqueue_tracks_starts_first_track_when_idle(self) -> None:
        first, second = make_track("first"), make_track("second")
        player = _make_player()
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            started = await player.enqueue_tracks(
                (first, second), None, placement="end"
            )
        self.assertIsNotNone(started)
        self.assertIs(_require_attempt(started).entry.track, first)
        self.assertEqual(_tracks(player), [second])
        play_mock.assert_awaited_once_with(
            first, start_time=0, volume=None, pause=False
        )

    async def test_enqueue_tracks_does_not_call_public_advance_recursively(
        self,
    ) -> None:
        player = _make_player()
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            await player.enqueue_tracks((make_track("first"),), None, placement="end")
        play_mock.assert_awaited_once()

    async def test_enqueue_tracks_does_not_interrupt_playing_track(self) -> None:
        current = make_entry("current")
        queued = make_track("queued")
        player = _make_player(current=current)
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            started = await player.enqueue_tracks((queued,), None, placement="end")
        self.assertIsNone(started)
        self.assertIs(player.current_entry, current)
        play_mock.assert_not_awaited()

    async def test_repeated_next_request_goes_before_previous_next_block(self) -> None:
        player = _make_player(current=make_entry("current"))
        first = [make_track("one"), make_track("two")]
        second = [make_track("three"), make_track("four")]
        await player.enqueue_tracks(first, None, placement="next")
        await player.enqueue_tracks(second, None, placement="next")
        self.assertEqual(_tracks(player), [*second, *first])

    async def test_concurrent_idle_enqueue_starts_only_one_initial_track(self) -> None:
        player = _make_player()
        entered, release = asyncio.Event(), asyncio.Event()

        async def play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await release.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as mocked:
            first = asyncio.create_task(
                player.enqueue_tracks((make_track("first"),), None, placement="end")
            )
            await entered.wait()
            second = asyncio.create_task(
                player.enqueue_tracks((make_track("second"),), None, placement="end")
            )
            await asyncio.sleep(0)
            release.set()
            first_result, second_result = await asyncio.gather(first, second)
        self.assertIsNotNone(first_result)
        self.assertIsNone(second_result)
        self.assertEqual(mocked.await_count, 1)
        self.assertEqual([track.identifier for track in _tracks(player)], ["second"])

    async def test_stale_end_off_does_not_replace_track_started_by_enqueue(
        self,
    ) -> None:
        player = _make_player()
        entered, release = asyncio.Event(), asyncio.Event()

        async def play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await release.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=play)):
            enqueue = asyncio.create_task(
                player.enqueue_tracks((make_track("new"),), None, placement="end")
            )
            await entered.wait()
            stale_end = asyncio.create_task(
                player.handle_track_end(make_track("old"), mafic.EndReason.FINISHED)
            )
            await asyncio.sleep(0)
            self.assertFalse(stale_end.done())
            release.set()
            _, outcome = await asyncio.gather(enqueue, stale_end)
        self.assertTrue(outcome.is_stale)
        self.assertEqual(_require_entry(player.current_entry).track.identifier, "new")

    async def test_stale_end_queue_does_not_append_or_interrupt_current(
        self,
    ) -> None:
        player = _make_player(current=make_entry("replacement"))
        player.repeat.mode = RepeatMode.QUEUE
        before = player.queue.snapshot()
        outcome = await player.handle_track_end(
            make_track("previous"), mafic.EndReason.FINISHED
        )
        self.assertTrue(outcome.is_stale)
        self.assertEqual(player.queue.snapshot(), before)

    async def test_stale_end_track_does_not_replay_or_reorder_queue(
        self,
    ) -> None:
        player = _make_player(current=make_entry("replacement"))
        player.repeat.mode = RepeatMode.TRACK
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                make_track("previous"), mafic.EndReason.FINISHED
            )
        self.assertTrue(outcome.is_stale)
        play_mock.assert_not_awaited()

    async def test_repeat_track_creates_new_attempt_for_each_finished_end(
        self,
    ) -> None:
        entry = make_entry("previous")
        player = _make_player(current=entry)
        player.repeat.mode = RepeatMode.TRACK
        with patch.object(player, "play", new=AsyncMock()):
            first = await player.handle_track_end(entry.track, mafic.EndReason.FINISHED)
            second = await player.handle_track_end(
                entry.track, mafic.EndReason.FINISHED
            )
        first_started = _require_attempt(first.started_attempt)
        second_started = _require_attempt(second.started_attempt)
        self.assertEqual(first_started.entry, entry)
        self.assertEqual(second_started.entry, entry)
        self.assertNotEqual(first_started.attempt_id, second_started.attempt_id)

    async def test_start_queued_if_idle_with_no_current_starts_next_track(self) -> None:
        player = _make_player()
        queued = make_entry("next")
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock()):
            started = await player.start_queued_if_idle()
        self.assertEqual(_require_attempt(started).entry, queued)

    async def test_load_failed_with_repeat_off_starts_next_and_preserves_queue(
        self,
    ) -> None:
        await self._assert_load_failed_advances(RepeatMode.OFF)

    async def test_load_failed_ignores_repeat_track(self) -> None:
        await self._assert_load_failed_advances(RepeatMode.TRACK)

    async def test_load_failed_ignores_repeat_queue_and_preserves_remaining(
        self,
    ) -> None:
        await self._assert_load_failed_advances(RepeatMode.QUEUE)

    async def _assert_load_failed_advances(self, mode: RepeatMode) -> None:
        failed = make_entry("failed")
        next_entry = make_entry("next", entry_id=2)
        remaining = make_entry("remaining", entry_id=3)
        player = _make_player(current=failed)
        player.repeat.mode = mode
        player.queue.extend((next_entry, remaining))
        with patch.object(player, "play", new=AsyncMock()):
            outcome = await player.handle_track_end(
                failed.track, mafic.EndReason.LOAD_FAILED
            )
        self.assertEqual(_require_attempt(outcome.started_attempt).entry, next_entry)
        self.assertEqual(player.queue.snapshot(), (remaining,))
        self.assertNotIn(failed, player.queue)

    async def test_load_failed_with_empty_queue_stops_player(self) -> None:
        failed = make_entry("failed")
        player = _make_player(current=failed)
        outcome = await player.handle_track_end(
            failed.track, mafic.EndReason.LOAD_FAILED
        )
        self.assertIsNone(outcome.started_attempt)
        self.assertIsNone(player.current_attempt)

    async def test_force_skip_ignores_repeat_track_mode(self) -> None:
        await self._assert_skip_mode(RepeatMode.TRACK)

    async def test_advance_with_empty_queue_stops_player(self) -> None:
        player = _make_player(current=make_entry("current"))
        with patch.object(mafic.Player, "stop", new=AsyncMock()) as stop_mock:
            _, started = await player.skip()
        self.assertIsNone(started)
        stop_mock.assert_awaited_once()

    async def test_force_skip_advances_to_next_track(self) -> None:
        await self._assert_skip_mode(RepeatMode.OFF)

    async def test_skip_starts_next_track(self) -> None:
        await self._assert_skip_mode(RepeatMode.OFF)

    async def _assert_skip_mode(self, mode: RepeatMode) -> None:
        current = make_entry("current")
        next_entry = make_entry("next", entry_id=2)
        player = _make_player(current=current)
        player.repeat.mode = mode
        player.queue.append(next_entry)
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            skipped, started = await player.skip()
        self.assertEqual(_require_attempt(skipped).entry, current)
        self.assertEqual(_require_attempt(started).entry, next_entry)
        self.assertEqual(player.current_entry, next_entry)
        self.assertEqual(player._pending_end_attempts[0].entry, current)
        play_mock.assert_awaited_once_with(
            next_entry.track, start_time=0, volume=None, pause=False
        )

    async def test_skip_with_empty_queue_stops_once_via_advance(self) -> None:
        current = make_entry("current", requester_id=7)
        player = _make_player(current=current)
        with patch.object(mafic.Player, "stop", new=AsyncMock()):
            skipped, started = await player.skip()
        self.assertEqual(_require_attempt(skipped).entry, current)
        self.assertIsNone(started)
        outcome = await player.handle_track_end(current.track, mafic.EndReason.STOPPED)
        ended = _require_attempt(outcome.ended_attempt)
        self.assertEqual(_require_requester(ended.entry).user_id, 7)

    async def test_skip_uses_current_under_lock_without_stale_end_guard(self) -> None:
        first = make_entry("same")
        second = make_entry("same", entry_id=2)
        player = _make_player(current=first)
        player.queue.append(second)
        with patch.object(player, "play", new=AsyncMock()):
            await player.skip()
        outcome = await player.handle_track_end(first.track, mafic.EndReason.STOPPED)
        self.assertEqual(_require_attempt(outcome.ended_attempt).entry, first)
        self.assertEqual(player.current_entry, second)

    async def test_equal_source_end_matches_pending_attempt_fifo_before_current(
        self,
    ) -> None:
        first = make_entry("same", entry_id=1, requester_id=10)
        second = make_entry("same", entry_id=2, requester_id=20)
        player = _make_player(current=first)
        player.queue.append(second)
        with patch.object(player, "play", new=AsyncMock()):
            await player.skip()

        # Mafic supplies only source identity here, so FIFO pending-first is the
        # deterministic best available match for two equal source tracks.
        outcome = await player.handle_track_end(
            make_track("same"), mafic.EndReason.STOPPED
        )

        self.assertEqual(_require_attempt(outcome.ended_attempt).entry, first)
        self.assertEqual(player.current_entry, second)
        self.assertEqual(
            _require_requester(_require_entry(player.current_entry)).user_id, 20
        )

    async def test_skip_waiting_behind_enqueue_returns_actual_transition(
        self,
    ) -> None:
        player = _make_player()
        entered, release = asyncio.Event(), asyncio.Event()

        async def play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await release.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=play)):
            enqueue = asyncio.create_task(
                player.enqueue_tracks(
                    (make_track("first"), make_track("second")),
                    None,
                    placement="end",
                )
            )
            await entered.wait()
            skip = asyncio.create_task(player.skip())
            release.set()
            enqueued, (skipped, started) = await asyncio.gather(enqueue, skip)
        self.assertEqual(
            _require_attempt(enqueued).entry, _require_attempt(skipped).entry
        )
        self.assertEqual(_require_attempt(started).entry.track.identifier, "second")

    async def test_skip_ignores_track_and_queue_repeat_modes(self) -> None:
        for mode in RepeatMode:
            with self.subTest(mode=mode):
                await self._assert_skip_mode(mode)

    async def test_rotate_current_preserves_queue_order_and_returns_started(
        self,
    ) -> None:
        current = make_entry("current")
        next_entry = make_entry("next", entry_id=2)
        existing = make_entry("existing", entry_id=3)
        player = _make_player(current=current)
        player.queue.extend((next_entry, existing))
        with patch.object(player, "play", new=AsyncMock()):
            moved, started = await player.rotate_current()
        self.assertEqual(_require_attempt(moved).entry, current)
        self.assertEqual(_require_attempt(started).entry, next_entry)
        self.assertEqual(player.queue.snapshot(), (existing, current))

    async def test_rotate_current_with_empty_queue_restarts_current(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        with patch.object(player, "play", new=AsyncMock()):
            moved, started = await player.rotate_current()
        self.assertEqual(_require_attempt(moved).entry, current)
        self.assertEqual(_require_attempt(started).entry, current)
        self.assertNotEqual(
            _require_attempt(player.current_attempt).attempt_id,
            player._pending_end_attempts[0].attempt_id,
        )

    async def test_stop_and_clear_clears_queue_and_calls_base_stop(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        player.queue.append(make_entry("queued", entry_id=2))
        with patch.object(mafic.Player, "stop", new=AsyncMock()) as stop_mock:
            await player.stop_and_clear()
        self.assertTrue(player.queue.is_empty)
        self.assertIsNone(player.current_attempt)
        self.assertEqual(player._pending_end_attempts[0].entry, current)
        stop_mock.assert_awaited_once()

    async def test_stop_then_enqueue_leaves_new_track_queued_until_stopped_event(
        self,
    ) -> None:
        current = make_entry("current")
        queued = make_track("queued-after-stop")
        player = _make_player(current=current)
        with (
            patch.object(mafic.Player, "stop", new=AsyncMock()),
            patch.object(player, "play", new=AsyncMock()) as play_mock,
        ):
            await player.stop_and_clear()
            started = await player.enqueue_tracks((queued,), None, placement="end")
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.STOPPED
            )
            duplicate = await player.handle_track_end(
                current.track, mafic.EndReason.STOPPED
            )
        self.assertIsNone(started)
        self.assertIsNotNone(outcome.started_attempt)
        self.assertTrue(duplicate.is_stale)
        self.assertEqual(play_mock.await_count, 1)

    async def test_current_cleanup_does_not_start_queued_entry(self) -> None:
        current = make_entry("current")
        queued = make_entry("queued", entry_id=2)
        player = _make_player(current=current)
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.CLEANUP
            )
        self.assertIsNone(outcome.started_attempt)
        self.assertEqual(player.queue.snapshot(), (queued,))
        play_mock.assert_not_awaited()

    async def test_pending_cleanup_does_not_start_queued_entry(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        with patch.object(mafic.Player, "stop", new=AsyncMock()):
            await player.stop_and_clear()
        player.queue.append(make_entry("queued", entry_id=2))
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.CLEANUP
            )
        self.assertIsNone(outcome.started_attempt)
        play_mock.assert_not_awaited()

    async def test_current_replaced_does_not_start_queued_entry(self) -> None:
        current = make_entry("current")
        queued = make_entry("queued", entry_id=2)
        player = _make_player(current=current)
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.REPLACED
            )
        self.assertIsNone(outcome.started_attempt)
        self.assertEqual(player.queue.snapshot(), (queued,))
        play_mock.assert_not_awaited()

    async def test_pending_replaced_does_not_start_queued_entry(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        with patch.object(mafic.Player, "stop", new=AsyncMock()):
            await player.stop_and_clear()
        player.queue.append(make_entry("queued", entry_id=2))
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.REPLACED
            )
        self.assertIsNone(outcome.started_attempt)
        play_mock.assert_not_awaited()

    async def test_exception_matching_prefers_current_over_equal_pending(self) -> None:
        pending = make_entry("same", entry_id=1, requester_id=10)
        current = make_entry("same", entry_id=2, requester_id=20)
        player = _make_player(current=pending)
        player.queue.append(current)
        with patch.object(player, "play", new=AsyncMock()):
            await player.skip()

        resolved = await player.claim_track_exception(make_track("same"))

        self.assertEqual(_require_attempt(resolved).entry, current)
        self.assertEqual(
            _require_requester(_require_attempt(resolved).entry).user_id, 20
        )

    async def test_matching_requires_source_and_identifier(self) -> None:
        current = make_entry("same")
        other_source = make_track("same")
        other_source.source = "other"
        player = _make_player(current=current)

        outcome = await player.handle_track_end(other_source, mafic.EndReason.FINISHED)

        self.assertTrue(outcome.is_stale)
        self.assertEqual(player.current_entry, current)

    async def test_start_queued_if_idle_starts_track_after_stopped_lifecycle(
        self,
    ) -> None:
        player = _make_player()
        queued = make_entry("queued")
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock()):
            started = await player.start_queued_if_idle()
        self.assertEqual(_require_attempt(started).entry, queued)

    async def test_start_queued_if_idle_with_empty_queue_does_not_play_or_stop(
        self,
    ) -> None:
        player = _make_player()
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            self.assertIsNone(await player.start_queued_if_idle())
        play_mock.assert_not_awaited()

    async def test_start_queued_if_idle_preserves_existing_current(self) -> None:
        current = make_entry("current")
        queued = make_entry("queued", entry_id=2)
        player = _make_player(current=current)
        player.queue.append(queued)
        self.assertIsNone(await player.start_queued_if_idle())
        self.assertEqual(player.queue.snapshot(), (queued,))

    async def test_repeat_track_replays_previous_track(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.TRACK
        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.FINISHED
            )
        self.assertEqual(_require_attempt(outcome.started_attempt).entry, current)
        play_mock.assert_awaited_once_with(
            current.track, start_time=0, volume=None, pause=False
        )

    async def test_repeat_queue_appends_previous_track_and_starts_next(self) -> None:
        current = make_entry("current")
        next_entry = make_entry("next", entry_id=2)
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.QUEUE
        player.queue.append(next_entry)
        with patch.object(player, "play", new=AsyncMock()):
            outcome = await player.handle_track_end(
                current.track, mafic.EndReason.FINISHED
            )
        self.assertEqual(_require_attempt(outcome.started_attempt).entry, next_entry)
        self.assertEqual(player.queue.snapshot(), (current,))

    async def test_requester_collision_creates_distinct_entries(self) -> None:
        track = make_track("same")
        player = _make_player(current=make_entry("current"))
        await player.enqueue_tracks((track,), TrackRequester(10), placement="end")
        await player.enqueue_tracks((track,), TrackRequester(20), placement="end")
        first, second = player.queue.snapshot()
        self.assertNotEqual(first.entry_id, second.entry_id)
        self.assertEqual(first.requester.user_id, 10)
        self.assertEqual(second.requester.user_id, 20)

    async def test_playlist_entries_share_requester_and_preserve_order(self) -> None:
        player = _make_player(current=make_entry("current"))
        requester = TrackRequester(10)
        tracks = [make_track("one"), make_track("two"), make_track("three")]
        await player.enqueue_tracks(tracks, requester, placement="next")
        entries = player.queue.snapshot()
        self.assertEqual([entry.track for entry in entries], tracks)
        self.assertTrue(all(entry.requester is requester for entry in entries))
        self.assertEqual(len({entry.entry_id for entry in entries}), 3)

    async def test_rotate_failure_rolls_back_all_local_state(self) -> None:
        await self._assert_manual_failure_rolls_back("rotate")

    async def test_skip_failure_rolls_back_all_local_state(self) -> None:
        await self._assert_manual_failure_rolls_back("skip")

    async def _assert_manual_failure_rolls_back(self, operation: str) -> None:
        current = make_entry("current")
        queued = make_entry("queued", entry_id=2)
        player = _make_player(current=current)
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                if operation == "rotate":
                    await player.rotate_current()
                else:
                    await player.skip()
        self.assertEqual(player.current_entry, current)
        self.assertEqual(player.queue.snapshot(), (queued,))
        self.assertEqual(list(player._pending_end_attempts), [])

    async def test_repeat_queue_failure_rolls_back_all_local_state(self) -> None:
        current = make_entry("current")
        queued = make_entry("queued", entry_id=2)
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.QUEUE
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                await player.handle_track_end(current.track, mafic.EndReason.FINISHED)
        self.assertEqual(player.current_entry, current)
        self.assertEqual(player.queue.snapshot(), (queued,))

    async def test_repeat_track_failure_rolls_back_all_local_state(self) -> None:
        current = make_entry("current")
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.TRACK
        with patch.object(player, "play", new=AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                await player.handle_track_end(current.track, mafic.EndReason.FINISHED)
        self.assertEqual(player.current_entry, current)
        self.assertTrue(player.queue.is_empty)

    async def test_queued_idle_start_failure_returns_entry_to_front(self) -> None:
        player = _make_player()
        queued = make_entry("queued")
        player.queue.append(queued)
        with patch.object(player, "play", new=AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                await player.start_queued_if_idle()
        self.assertIsNone(player.current_entry)
        self.assertEqual(player.queue.snapshot(), (queued,))

    async def test_idle_enqueue_failure_preserves_new_playlist_entries(self) -> None:
        player = _make_player()
        tracks = (make_track("one"), make_track("two"))
        requester = TrackRequester(7)
        with patch.object(player, "play", new=AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                await player.enqueue_tracks(tracks, requester, placement="end")
        entries = player.queue.snapshot()
        self.assertEqual([entry.track for entry in entries], list(tracks))
        self.assertTrue(all(entry.requester is requester for entry in entries))
        self.assertIsNone(player.current_entry)

    async def test_restore_entries_advances_next_entry_id_without_attempt_restore(
        self,
    ) -> None:
        player = _make_player()
        current = make_entry("current", entry_id=8)
        queued = make_entry("queued", entry_id=12)
        player.restore_entries(current, [queued])

        with patch.object(player, "play", new=AsyncMock()):
            started = await player.restore_playback(
                current, start_time=0, volume=50, pause=True
            )
            await player.enqueue_tracks((make_track("new"),), None, placement="end")

        self.assertEqual(started.attempt_id, 1)
        self.assertEqual(player.queue.snapshot()[-1].entry_id, 13)

    async def test_repeated_restore_replaces_current_without_creating_pending(
        self,
    ) -> None:
        player = _make_player()
        entry = make_entry("restored")
        unrelated = PlaybackAttempt(99, make_entry("unrelated", entry_id=99))
        player._pending_end_attempts.append(unrelated)
        with patch.object(player, "play", new=AsyncMock()):
            first = await player.restore_playback(
                entry, start_time=0, volume=50, pause=False
            )
            player._exception_attempt_ids.add(first.attempt_id)
            second = await player.restore_playback(
                entry, start_time=0, volume=50, pause=False
            )
            outcome = await player.handle_track_end(
                entry.track, mafic.EndReason.FINISHED
            )

        self.assertEqual(list(player._pending_end_attempts), [unrelated])
        self.assertNotIn(first.attempt_id, player._exception_attempt_ids)
        self.assertEqual(outcome.ended_attempt, second)
        self.assertIsNone(player.current_attempt)

    async def test_repeated_restore_failure_restores_current_and_pending(self) -> None:
        player = _make_player()
        entry = make_entry("restored")
        unrelated = PlaybackAttempt(99, make_entry("unrelated", entry_id=99))
        player._pending_end_attempts.append(unrelated)
        with patch.object(
            player,
            "play",
            new=AsyncMock(side_effect=[None, RuntimeError("failed")]),
        ):
            first = await player.restore_playback(
                entry, start_time=0, volume=50, pause=False
            )
            player._exception_attempt_ids.add(first.attempt_id)
            with self.assertRaises(RuntimeError):
                await player.restore_playback(
                    entry, start_time=0, volume=50, pause=False
                )

        self.assertIs(player.current_attempt, first)
        self.assertEqual(list(player._pending_end_attempts), [unrelated])
        self.assertIn(first.attempt_id, player._exception_attempt_ids)

    async def test_idle_enqueue_cancellation_restores_playlist_entries(self) -> None:
        player = _make_player()
        tracks = (make_track("one"), make_track("two"))
        requester = TrackRequester(7, 8)
        entered = asyncio.Event()
        never_finish = asyncio.Event()

        async def blocked_play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await never_finish.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=blocked_play)):
            task = asyncio.create_task(
                player.enqueue_tracks(tracks, requester, placement="end")
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        entries = player.queue.snapshot()
        self.assertIsNone(player.current_attempt)
        self.assertEqual([entry.track for entry in entries], list(tracks))
        self.assertTrue(all(entry.requester is requester for entry in entries))

    async def test_skip_cancellation_restores_transition_state(self) -> None:
        current = make_entry("current")
        next_entry = make_entry("next", entry_id=2)
        unrelated = PlaybackAttempt(99, make_entry("unrelated", entry_id=99))
        player = _make_player(current=current)
        player.queue.append(next_entry)
        player._pending_end_attempts.append(unrelated)
        entered = asyncio.Event()
        never_finish = asyncio.Event()

        async def blocked_play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await never_finish.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=blocked_play)):
            task = asyncio.create_task(player.skip())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(player.current_entry, current)
        self.assertEqual(list(player._pending_end_attempts), [unrelated])
        self.assertEqual(player.queue.snapshot(), (next_entry,))
        self.assertNotIn(current, player.queue)

    async def test_repeat_queue_cancellation_restores_transition_state(self) -> None:
        current = make_entry("current")
        next_entry = make_entry("next", entry_id=2)
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.QUEUE
        player.queue.append(next_entry)
        entered = asyncio.Event()
        never_finish = asyncio.Event()

        async def blocked_play(*_args: object, **_kwargs: object) -> None:
            entered.set()
            await never_finish.wait()

        with patch.object(player, "play", new=AsyncMock(side_effect=blocked_play)):
            task = asyncio.create_task(
                player.handle_track_end(current.track, mafic.EndReason.FINISHED)
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(player.current_entry, current)
        self.assertEqual(player.queue.snapshot(), (next_entry,))
        self.assertEqual(list(player._pending_end_attempts), [])

    async def test_repeated_restore_cancellation_restores_previous_attempt(
        self,
    ) -> None:
        player = _make_player()
        entry = make_entry("restored")
        unrelated = PlaybackAttempt(99, make_entry("unrelated", entry_id=99))
        entered = asyncio.Event()
        never_finish = asyncio.Event()
        play_count = 0

        async def second_play_blocks(*_args: object, **_kwargs: object) -> None:
            nonlocal play_count
            play_count += 1
            if play_count == 1:
                return
            entered.set()
            await never_finish.wait()

        with patch.object(
            player, "play", new=AsyncMock(side_effect=second_play_blocks)
        ):
            first = await player.restore_playback(
                entry, start_time=0, volume=50, pause=False
            )
            player._pending_end_attempts.append(unrelated)
            player._exception_attempt_ids.add(first.attempt_id)
            task = asyncio.create_task(
                player.restore_playback(entry, start_time=0, volume=50, pause=False)
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertIs(player.current_attempt, first)
        self.assertEqual(list(player._pending_end_attempts), [unrelated])
        self.assertIn(first.attempt_id, player._exception_attempt_ids)

    async def test_voice_server_update_propagates_error_without_cleanup(
        self,
    ) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        with (
            patch.object(player, "cleanup") as cleanup,
            patch.object(
                mafic.Player,
                "on_voice_server_update",
                new=AsyncMock(side_effect=aiohttp.ClientConnectionError("down")),
            ),
            self.assertRaises(aiohttp.ClientConnectionError),
        ):
            await player.on_voice_server_update(
                cast(VoiceServerUpdatePayload, object())
            )
        cleanup.assert_not_called()

    async def test_update_propagates_error_without_cleanup(self) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        with (
            patch.object(player, "cleanup") as cleanup,
            patch.object(
                mafic.Player,
                "update",
                new=AsyncMock(side_effect=mafic.HTTPNotFound("Session not found")),
            ),
            self.assertRaises(mafic.HTTPNotFound),
        ):
            await player.update(pause=True)
        cleanup.assert_not_called()
