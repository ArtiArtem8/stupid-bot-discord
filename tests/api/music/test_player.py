"""Tests for custom music player failure cleanup."""

import asyncio
import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic
from discord.types.voice import VoiceServerUpdate as VoiceServerUpdatePayload

from api.music.models import RepeatMode
from api.music.player import MusicPlayer
from api.music.queue import QueueManager, RepeatManager
from tests.api.music.helpers import make_track


def _make_player(*, current: mafic.Track | None = None) -> MusicPlayer:
    player = MusicPlayer.__new__(MusicPlayer)
    player.queue = QueueManager()
    player.repeat = RepeatManager(RepeatMode.OFF)
    player._requester_map = {}
    player._transition_lock = asyncio.Lock()
    player._current = current
    player.guild = MagicMock(id=123)
    return player


class TestMusicPlayer(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_tracks_with_next_adds_single_track_to_front(self) -> None:
        existing = make_track("existing")
        track = make_track("next")
        current = make_track("current")
        player = _make_player(current=current)
        player.queue.append(existing)

        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            started = await player.enqueue_tracks((track,), placement="next")

        self.assertIsNone(started)
        self.assertIs(player.current, current)
        self.assertEqual(list(player.queue), [track, existing])
        play_mock.assert_not_awaited()

    async def test_enqueue_tracks_with_next_preserves_playlist_order_at_front(
        self,
    ) -> None:
        existing = make_track("existing")
        tracks = [make_track("one"), make_track("two"), make_track("three")]
        player = _make_player(current=make_track("current"))
        player.queue.append(existing)

        started = await player.enqueue_tracks(tracks, placement="next")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [*tracks, existing])

    async def test_enqueue_tracks_with_end_adds_playlist_to_back(self) -> None:
        existing = make_track("existing")
        tracks = [make_track("one"), make_track("two")]
        player = _make_player(current=make_track("current"))
        player.queue.append(existing)

        started = await player.enqueue_tracks(tracks, placement="end")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [existing, *tracks])

    async def test_enqueue_tracks_with_empty_sequence_returns_none(self) -> None:
        player = _make_player(current=make_track("current"))

        started = await player.enqueue_tracks((), placement="end")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [])

    async def test_enqueue_tracks_starts_first_track_when_idle(self) -> None:
        first = make_track("first")
        second = make_track("second")
        player = _make_player()

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.enqueue_tracks((first, second), placement="end")

        self.assertIs(started, first)
        play_mock.assert_awaited_once_with(first, start_time=0)
        self.assertEqual(list(player.queue), [second])

    async def test_enqueue_tracks_does_not_call_public_advance_recursively(
        self,
    ) -> None:
        track = make_track("first")
        player = _make_player()

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with (
            patch.object(player, "advance", new=AsyncMock()) as advance_mock,
            patch.object(player, "play", new=AsyncMock(side_effect=play)),
        ):
            started = await player.enqueue_tracks((track,), placement="end")

        self.assertIs(started, track)
        advance_mock.assert_not_awaited()

    async def test_enqueue_tracks_does_not_interrupt_playing_track(self) -> None:
        current = make_track("current")
        track = make_track("queued")
        player = _make_player(current=current)

        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            started = await player.enqueue_tracks((track,), placement="end")

        self.assertIsNone(started)
        self.assertIs(player.current, current)
        play_mock.assert_not_awaited()
        self.assertEqual(list(player.queue), [track])

    async def test_repeated_next_request_goes_before_previous_next_block(self) -> None:
        first_block = [make_track("one"), make_track("two")]
        second_block = [make_track("three"), make_track("four")]
        player = _make_player(current=make_track("current"))

        await player.enqueue_tracks(first_block, placement="next")
        await player.enqueue_tracks(second_block, placement="next")

        self.assertEqual(list(player.queue), [*second_block, *first_block])

    async def test_concurrent_idle_enqueue_starts_only_one_initial_track(self) -> None:
        first = make_track("first")
        second = make_track("second")
        player = _make_player()
        play_entered = asyncio.Event()
        release_play = asyncio.Event()

        async def play(track: mafic.Track, **_: object) -> None:
            play_entered.set()
            await release_play.wait()
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            first_task = asyncio.create_task(
                player.enqueue_tracks((first,), placement="end")
            )
            await play_entered.wait()
            second_task = asyncio.create_task(
                player.enqueue_tracks((second,), placement="end")
            )
            await asyncio.sleep(0)
            release_play.set()

            started_first, started_second = await asyncio.gather(
                first_task, second_task
            )

        self.assertIs(started_first, first)
        self.assertIsNone(started_second)
        play_mock.assert_awaited_once_with(first, start_time=0)
        self.assertEqual(list(player.queue), [second])

    async def test_stale_end_off_does_not_replace_track_started_by_enqueue(
        self,
    ) -> None:
        old_track = make_track("old")
        new_track = make_track("new")
        player = _make_player()
        play_entered = asyncio.Event()
        release_play = asyncio.Event()

        async def play(track: mafic.Track, **_: object) -> None:
            play_entered.set()
            await release_play.wait()
            player._current = track

        with (
            patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock,
            patch.object(player, "stop", new=AsyncMock()) as stop_mock,
        ):
            enqueue_task = asyncio.create_task(
                player.enqueue_tracks((new_track,), placement="end")
            )
            await play_entered.wait()
            stale_advance_task = asyncio.create_task(
                player.advance_after_end(old_track)
            )
            await asyncio.sleep(0)
            release_play.set()

            started, stale_result = await asyncio.gather(
                enqueue_task,
                stale_advance_task,
            )

        self.assertIs(started, new_track)
        self.assertIsNone(stale_result)
        play_mock.assert_awaited_once_with(new_track, start_time=0)
        stop_mock.assert_not_awaited()

    async def test_stale_end_queue_appends_previous_without_interrupting_current(
        self,
    ) -> None:
        previous = make_track("previous")
        replacement = make_track("replacement")
        queued = make_track("queued")
        player = _make_player(current=replacement)
        player.repeat.mode = RepeatMode.QUEUE
        player.queue.append(queued)

        with (
            patch.object(player, "play", new=AsyncMock()) as play_mock,
            patch.object(player, "stop", new=AsyncMock()) as stop_mock,
        ):
            started = await player.advance_after_end(previous)

        self.assertIsNone(started)
        self.assertIs(player.current, replacement)
        self.assertEqual(list(player.queue), [queued, previous])
        play_mock.assert_not_awaited()
        stop_mock.assert_not_awaited()

    async def test_stale_end_track_replays_previous_and_queues_replacement_first(
        self,
    ) -> None:
        previous = make_track("previous")
        replacement = make_track("replacement-1")
        next_replacement = make_track("replacement-2")
        existing = make_track("existing")
        player = _make_player(current=replacement)
        player.repeat.mode = RepeatMode.TRACK
        player.queue.extend((next_replacement, existing))

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with (
            patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock,
            patch.object(player, "stop", new=AsyncMock()) as stop_mock,
        ):
            started = await player.advance_after_end(previous)

        self.assertIs(started, previous)
        self.assertIs(player.current, previous)
        self.assertEqual(list(player.queue), [replacement, next_replacement, existing])
        play_mock.assert_awaited_once_with(previous, start_time=0)
        stop_mock.assert_not_awaited()

    async def test_stale_end_track_repeats_previous_again_after_replay_ends(
        self,
    ) -> None:
        previous = make_track("previous")
        replacement = make_track("replacement")
        queued = make_track("queued")
        player = _make_player(current=replacement)
        player.repeat.mode = RepeatMode.TRACK
        player.queue.append(queued)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            first_started = await player.advance_after_end(previous)
            player._current = None
            second_started = await player.advance_after_end(previous)

        self.assertIs(first_started, previous)
        self.assertIs(second_started, previous)
        self.assertIs(player.current, previous)
        self.assertEqual(list(player.queue), [replacement, queued])
        self.assertEqual(play_mock.await_count, 2)
        play_mock.assert_awaited_with(previous, start_time=0)

    async def test_advance_after_end_with_no_current_starts_next_track(self) -> None:
        previous = make_track("previous")
        next_track = make_track("next")
        player = _make_player()
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance_after_end(previous)

        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_force_skip_ignores_repeat_track_mode(self) -> None:
        current = make_track("current")
        next_track = make_track("next")
        player = _make_player(current=current)
        player.repeat.mode = RepeatMode.TRACK
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(
                force_skip=True,
                previous_track=current,
            )

        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_advance_with_empty_queue_stops_player(self) -> None:
        previous = make_track("previous")
        player = _make_player()

        with patch.object(player, "stop", new=AsyncMock()) as stop_mock:
            started = await player.advance_after_end(previous)

        self.assertIsNone(started)
        stop_mock.assert_awaited_once()

    async def test_force_skip_advances_to_next_track(self) -> None:
        current = make_track("current")
        next_track = make_track("next")
        player = _make_player(current=current)
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(
                force_skip=True,
                previous_track=current,
            )

        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_skip_starts_next_track(self) -> None:
        current = make_track("current")
        next_track = make_track("next")
        player = _make_player(current=current)
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            skipped, started = await player.skip()

        self.assertIs(skipped, current)
        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_skip_with_empty_queue_stops_once_via_advance(self) -> None:
        current = make_track("current")
        player = _make_player(current=current)

        with patch.object(player, "stop", new=AsyncMock()) as stop_mock:
            skipped, started = await player.skip()

        self.assertIs(skipped, current)
        self.assertIsNone(started)
        stop_mock.assert_awaited_once()

    async def test_skip_uses_current_under_lock_without_stale_end_guard(self) -> None:
        skipped_track = make_track("same")
        replacement = make_track("same")
        next_track = make_track("next")
        player = _make_player(current=skipped_track)
        player.queue.append(next_track)
        await player._transition_lock.acquire()

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with (
            patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock,
            patch.object(player, "stop", new=AsyncMock()) as stop_mock,
        ):
            skip_task = asyncio.create_task(player.skip())
            await asyncio.sleep(0)
            player._current = replacement
            player._transition_lock.release()
            skipped, started = await skip_task

        self.assertIs(skipped, replacement)
        self.assertIs(started, next_track)
        self.assertIs(player.current, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)
        stop_mock.assert_not_awaited()

    async def test_skip_waiting_behind_enqueue_returns_actual_transition(
        self,
    ) -> None:
        first = make_track("first")
        second = make_track("second")
        player = _make_player()
        play_entered = asyncio.Event()
        release_first_play = asyncio.Event()

        async def play(track: mafic.Track, **_: object) -> None:
            play_entered.set()
            await release_first_play.wait()
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            enqueue_task = asyncio.create_task(
                player.enqueue_tracks((first, second), placement="end")
            )
            await play_entered.wait()
            skip_task = asyncio.create_task(player.skip())
            await asyncio.sleep(0)
            release_first_play.set()

            enqueued, (skipped, started) = await asyncio.gather(
                enqueue_task,
                skip_task,
            )

        self.assertIs(enqueued, first)
        self.assertIs(skipped, first)
        self.assertIs(started, second)
        self.assertIs(player.current, second)
        self.assertEqual(play_mock.await_count, 2)

    async def test_skip_ignores_track_and_queue_repeat_modes(self) -> None:
        def make_play_side_effect(
            target: MusicPlayer,
        ) -> AsyncMock:
            async def play(track: mafic.Track, **_: object) -> None:
                target._current = track

            return AsyncMock(side_effect=play)

        for repeat_mode in (RepeatMode.OFF, RepeatMode.TRACK, RepeatMode.QUEUE):
            with self.subTest(repeat_mode=repeat_mode):
                current = make_track(f"current-{repeat_mode.value}")
                next_track = make_track(f"next-{repeat_mode.value}")
                player = _make_player(current=current)
                player.repeat.mode = repeat_mode
                player.queue.append(next_track)

                with patch.object(
                    player,
                    "play",
                    new=make_play_side_effect(player),
                ) as play_mock:
                    skipped, started = await player.skip()

                self.assertIs(skipped, current)
                self.assertIs(started, next_track)
                self.assertIs(player.current, next_track)
                self.assertEqual(list(player.queue), [])
                play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_rotate_current_preserves_queue_order_and_returns_started(
        self,
    ) -> None:
        current = make_track("current")
        next_track = make_track("next")
        existing = make_track("existing")
        player = _make_player(current=current)
        player.queue.extend((next_track, existing))

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            moved, started = await player.rotate_current()

        self.assertIs(moved, current)
        self.assertIs(started, next_track)
        self.assertIs(player.current, next_track)
        self.assertEqual(list(player.queue), [existing, current])
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_rotate_current_with_empty_queue_restarts_current(self) -> None:
        current = make_track("current")
        player = _make_player(current=current)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            moved, started = await player.rotate_current()

        self.assertIs(moved, current)
        self.assertIs(started, current)
        self.assertIs(player.current, current)
        self.assertEqual(list(player.queue), [])
        play_mock.assert_awaited_once_with(current, start_time=0)

    async def test_stop_and_clear_clears_queue_and_stops_under_lock(self) -> None:
        current = make_track("current")
        queued = make_track("queued")
        player = _make_player(current=current)
        player.queue.append(queued)
        observed_queue_after_clear: list[mafic.Track] | None = None

        async def stop() -> None:
            nonlocal observed_queue_after_clear
            observed_queue_after_clear = list(player.queue)
            player._current = None

        with patch.object(mafic.Player, "stop", new=AsyncMock(side_effect=stop)):
            await player.stop_and_clear()

        self.assertEqual(observed_queue_after_clear, [])
        self.assertIsNone(player.current)
        self.assertEqual(list(player.queue), [])

    async def test_concurrent_stop_after_enqueue_leaves_idle_empty_queue(self) -> None:
        first = make_track("first")
        second = make_track("second")
        player = _make_player()
        play_entered = asyncio.Event()
        release_play = asyncio.Event()

        async def play(track: mafic.Track, **_: object) -> None:
            play_entered.set()
            await release_play.wait()
            player._current = track

        async def stop() -> None:
            player._current = None

        with (
            patch.object(player, "play", new=AsyncMock(side_effect=play)),
            patch.object(mafic.Player, "stop", new=AsyncMock(side_effect=stop)),
        ):
            enqueue_task = asyncio.create_task(
                player.enqueue_tracks((first, second), placement="end")
            )
            await play_entered.wait()
            stop_task = asyncio.create_task(player.stop_and_clear())
            await asyncio.sleep(0)
            release_play.set()
            await asyncio.gather(enqueue_task, stop_task)

        self.assertIsNone(player.current)
        self.assertEqual(list(player.queue), [])

    async def test_repeat_track_replays_previous_track(self) -> None:
        previous = make_track("previous")
        player = _make_player()
        player.repeat.mode = RepeatMode.TRACK

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(previous_track=previous)

        self.assertIs(started, previous)
        play_mock.assert_awaited_once_with(previous, start_time=0)

    async def test_repeat_queue_appends_previous_track_and_starts_next(self) -> None:
        previous = make_track("previous")
        next_track = make_track("next")
        player = _make_player()
        player.repeat.mode = RepeatMode.QUEUE
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(previous_track=previous)

        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)
        self.assertEqual(list(player.queue), [previous])

    async def test_voice_server_update_suppresses_client_connection_error(
        self,
    ) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            mafic.Player,
            "on_voice_server_update",
            new=AsyncMock(side_effect=aiohttp.ClientConnectionError("down")),
        ):
            await player.on_voice_server_update(
                cast(VoiceServerUpdatePayload, object())
            )

        player.cleanup.assert_called_once()

    async def test_voice_server_update_suppresses_http_not_found(self) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            mafic.Player,
            "on_voice_server_update",
            new=AsyncMock(side_effect=mafic.HTTPNotFound("Session not found")),
        ):
            await player.on_voice_server_update(
                cast(VoiceServerUpdatePayload, object())
            )

        player.cleanup.assert_called_once()

    async def test_update_does_not_call_remote_disconnect_after_http_not_found(
        self,
    ) -> None:
        player = MusicPlayer.__new__(MusicPlayer)
        player.cleanup = MagicMock()  # type: ignore[method-assign]
        player.disconnect = AsyncMock(  # type: ignore[method-assign]
            side_effect=mafic.HTTPNotFound("Session not found")
        )

        with patch.object(
            mafic.Player,
            "update",
            new=AsyncMock(side_effect=mafic.HTTPNotFound("Session not found")),
        ):
            with self.assertRaises(mafic.HTTPNotFound):
                await player.update(pause=True)

        player.cleanup.assert_called_once()
        player.disconnect.assert_not_awaited()
