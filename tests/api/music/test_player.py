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


def _make_track(identifier: str, *, length: int = 1000) -> mafic.Track:
    return mafic.Track(
        track_id=f"encoded-{identifier}",
        identifier=identifier,
        seekable=True,
        author="artist",
        length=length,
        stream=False,
        position=0,
        title=f"Track {identifier}",
        uri=f"https://example.com/{identifier}",
        artwork_url=None,
        isrc=None,
        source="test",
    )


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
        existing = _make_track("existing")
        track = _make_track("next")
        player = _make_player(current=_make_track("current"))
        player.queue.append(existing)

        started = await player.enqueue_tracks((track,), placement="next")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [track, existing])

    async def test_enqueue_tracks_with_next_preserves_playlist_order_at_front(
        self,
    ) -> None:
        existing = _make_track("existing")
        tracks = [_make_track("one"), _make_track("two"), _make_track("three")]
        player = _make_player(current=_make_track("current"))
        player.queue.append(existing)

        started = await player.enqueue_tracks(tracks, placement="next")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [*tracks, existing])

    async def test_enqueue_tracks_with_end_adds_playlist_to_back(self) -> None:
        existing = _make_track("existing")
        tracks = [_make_track("one"), _make_track("two")]
        player = _make_player(current=_make_track("current"))
        player.queue.append(existing)

        started = await player.enqueue_tracks(tracks, placement="end")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [existing, *tracks])

    async def test_enqueue_tracks_with_empty_sequence_returns_none(self) -> None:
        player = _make_player(current=_make_track("current"))

        started = await player.enqueue_tracks((), placement="end")

        self.assertIsNone(started)
        self.assertEqual(list(player.queue), [])

    async def test_enqueue_tracks_starts_first_track_when_idle(self) -> None:
        first = _make_track("first")
        second = _make_track("second")
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
        track = _make_track("first")
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
        current = _make_track("current")
        track = _make_track("queued")
        player = _make_player(current=current)

        with patch.object(player, "play", new=AsyncMock()) as play_mock:
            started = await player.enqueue_tracks((track,), placement="end")

        self.assertIsNone(started)
        self.assertIs(player.current, current)
        play_mock.assert_not_awaited()
        self.assertEqual(list(player.queue), [track])

    async def test_repeated_next_request_goes_before_previous_next_block(self) -> None:
        first_block = [_make_track("one"), _make_track("two")]
        second_block = [_make_track("three"), _make_track("four")]
        player = _make_player(current=_make_track("current"))

        await player.enqueue_tracks(first_block, placement="next")
        await player.enqueue_tracks(second_block, placement="next")

        self.assertEqual(list(player.queue), [*second_block, *first_block])

    async def test_concurrent_idle_enqueue_starts_only_one_initial_track(self) -> None:
        first = _make_track("first")
        second = _make_track("second")
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

    async def test_stale_advance_does_not_replace_track_started_by_enqueue(
        self,
    ) -> None:
        old_track = _make_track("old")
        new_track = _make_track("new")
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
                player.advance(previous_track=old_track)
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

    async def test_advance_after_finished_track_starts_next_track(self) -> None:
        previous = _make_track("previous")
        next_track = _make_track("next")
        player = _make_player()
        player.queue.append(next_track)

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(previous_track=previous)

        self.assertIs(started, next_track)
        play_mock.assert_awaited_once_with(next_track, start_time=0)

    async def test_force_skip_advances_to_next_track(self) -> None:
        current = _make_track("current")
        next_track = _make_track("next")
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

    async def test_repeat_track_replays_previous_track(self) -> None:
        previous = _make_track("previous")
        player = _make_player()
        player.repeat.mode = RepeatMode.TRACK

        async def play(track: mafic.Track, **_: object) -> None:
            player._current = track

        with patch.object(player, "play", new=AsyncMock(side_effect=play)) as play_mock:
            started = await player.advance(previous_track=previous)

        self.assertIs(started, previous)
        play_mock.assert_awaited_once_with(previous, start_time=0)

    async def test_repeat_queue_appends_previous_track_and_starts_next(self) -> None:
        previous = _make_track("previous")
        next_track = _make_track("next")
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
