"""Tests for soft music service availability failures."""

import asyncio
import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import mafic

from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    ControllerDestroyReason,
    MusicResult,
    MusicResultStatus,
    PlaybackAttempt,
    QueuePlacement,
    TrackRequester,
    VoiceCheckResult,
)
from api.music.service.core_service import CoreMusicService
from tests.api.music.helpers import make_entry, make_playlist, make_track


class TestCoreMusicServiceAvailability(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.connection = MagicMock()
        self.connection.ensure_available = AsyncMock(return_value=False)
        self.connection.start_lazy_connect = MagicMock()
        self.connection.cleanup = AsyncMock()
        self.connection.get_player = MagicMock(return_value=None)
        self.connection.is_known_unavailable = MagicMock(return_value=True)
        self.connection.is_player_usable = MagicMock(return_value=False)
        self.connection.get_player_node = MagicMock(return_value=None)
        self.connection.mark_node_unavailable = AsyncMock()
        self.connection.detach_stale_voice_client = AsyncMock()
        self.connection.invalidate_player = AsyncMock()
        self.connection.invalidate_node_and_players = AsyncMock()
        self.state = MagicMock()
        self.volume_repo = MagicMock()
        self.events = MagicMock()
        self.ui = MagicMock()
        self.service = CoreMusicService(
            self.bot,
            self.connection,
            self.state,
            self.volume_repo,
            self.events,
            self.ui,
        )

    async def _assert_apply_volume_error_is_soft_failure(
        self,
        error: Exception,
    ) -> None:
        guild = MagicMock(id=123)
        channel = MagicMock()
        player = MagicMock()
        player.guild = guild
        player.connected = True
        player.set_volume = AsyncMock(side_effect=error)
        player.cleanup = MagicMock()
        guild.voice_client = player

        self.connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player
        self.volume_repo.get_volume = AsyncMock(return_value=80)

        async def invalidate_player(failed_player: object) -> None:
            self.assertIs(failed_player, player)
            self.assertIs(guild.voice_client, player)
            player.cleanup.assert_not_called()

        self.connection.invalidate_player.side_effect = invalidate_player

        result = await self.service.join(guild, channel)

        self.assertEqual(
            result,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        self.connection.invalidate_player.assert_awaited_once_with(player)
        player.set_volume.assert_awaited_once_with(80)
        player.cleanup.assert_not_called()
        self.connection.invalidate_node_and_players.assert_not_awaited()
        self.connection.get_player_node.assert_not_called()
        self.connection.mark_node_unavailable.assert_not_awaited()
        self.connection.detach_stale_voice_client.assert_not_awaited()

    async def test_player_io_failure_uses_player_scope_with_safe_message(
        self,
    ) -> None:
        player = MagicMock()
        player_disconnected_message = (
            "Плеер потерял соединение. Попробуйте запустить трек ещё раз."
        )
        cases = (
            (
                aiohttp.ClientConnectionError("down"),
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
            ),
            (TimeoutError("timed out"), MUSIC_SERVICE_UNAVAILABLE_MESSAGE),
            (mafic.HTTPNotFound("missing"), player_disconnected_message),
            (mafic.PlayerNotConnected(), player_disconnected_message),
            (mafic.PlayerException("failed"), player_disconnected_message),
        )

        for error, expected_message in cases:
            with self.subTest(error=type(error).__name__):
                self.connection.invalidate_node_and_players.reset_mock()
                self.connection.invalidate_player.reset_mock()

                result: MusicResult[
                    object
                ] = await self.service._handle_player_io_failure(player, error)

                self.connection.invalidate_player.assert_awaited_once_with(player)
                self.connection.invalidate_node_and_players.assert_not_awaited()
                self.assertIs(result.status, MusicResultStatus.FAILURE)
                self.assertEqual(result.message, expected_message)

        self.connection.get_player_node.assert_not_called()
        self.connection.mark_node_unavailable.assert_not_awaited()
        self.connection.detach_stale_voice_client.assert_not_awaited()

    async def test_player_operation_failure_does_not_remove_shared_node(self) -> None:
        node = MagicMock(available=True)
        player_a = MagicMock(is_stale=False, _node=node)
        player_b = MagicMock(is_stale=False, _node=node)
        player_b.disconnect = AsyncMock()
        node.players = [player_a, player_b]

        async def invalidate_failed_player(player: object) -> None:
            self.assertIs(player, player_a)
            player_a.is_stale = True

        self.connection.invalidate_player.side_effect = invalidate_failed_player

        await self.service._handle_player_io_failure(
            player_a,
            aiohttp.ClientConnectionError("guild A request failed"),
        )

        self.connection.invalidate_player.assert_awaited_once_with(player_a)
        self.connection.invalidate_node_and_players.assert_not_awaited()
        self.connection.mark_node_unavailable.assert_not_awaited()
        self.assertTrue(node.available)
        self.assertTrue(player_a.is_stale)
        self.assertFalse(player_b.is_stale)
        player_b.disconnect.assert_not_awaited()

    async def test_initialize_does_not_raise_when_connection_unavailable(self) -> None:
        await self.service.initialize()

        self.events.setup.assert_called_once()
        self.assertTrue(self.service._initialized)
        self.connection.ensure_available.assert_not_awaited()
        self.connection.start_lazy_connect.assert_called_once()

    async def test_play_returns_unavailable_join_failure_without_player_lookup(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        channel = MagicMock()
        self.connection.join = AsyncMock(
            return_value=(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None)
        )

        result = await self.service.play(guild, channel, "query", 1, 2)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(
            result.data,
            (VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None),
        )
        self.connection.get_player.assert_not_called()

    async def test_play_returns_lost_player_after_successful_join(self) -> None:
        guild = MagicMock(id=123)
        self.connection.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))

        result = await self.service.play(guild, MagicMock(), "query", 1, 2)

        self.assertIs(result.status, MusicResultStatus.ERROR)
        self.assertIn("Плеер потерял соединение", result.message)

    async def test_play_returns_failure_for_empty_fetch(self) -> None:
        guild = MagicMock(id=123)
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=[])
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(guild, MagicMock(), "query", 1, 2)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, "Nothing found")

    async def test_play_track_load_failure_keeps_current_player_and_controller(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        current_track = make_track("current")
        error = mafic.TrackLoadException(
            message="load failed",
            severity="COMMON",
            cause="backend detail",
        )
        player = MagicMock(current=current_track, is_stale=False)
        player.fetch_tracks = AsyncMock(side_effect=error)
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player
        self.ui.controller.destroy_for_guild = AsyncMock()

        with patch.object(self.service, "join", join):
            result = await self.service.play(
                guild,
                MagicMock(),
                "query",
                requester_id=1,
                text_channel_id=2,
            )

        self.connection.invalidate_player.assert_not_awaited()
        self.connection.invalidate_node_and_players.assert_not_awaited()
        self.assertFalse(player.is_stale)
        self.assertIs(player.current, current_track)
        self.ui.controller.destroy_for_guild.assert_not_awaited()
        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(
            result.message,
            "Не удалось загрузить трек. Источник временно недоступен или не ответил.",
        )

    async def test_play_enqueues_single_track_at_end_by_default(self) -> None:
        guild = MagicMock(id=123)
        track = make_track("track")
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=[track])
        player.enqueue_tracks = AsyncMock(return_value=MagicMock())
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(guild, MagicMock(), "query", 1, 2)

        player.enqueue_tracks.assert_awaited_once_with(
            (track,), TrackRequester(1, 2), placement="end"
        )
        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        data = result.data
        if data is None or isinstance(data, tuple):
            self.fail("Expected play response data")
        self.assertEqual(data["placement"], "now")

    async def test_play_next_single_track_uses_next_placement(self) -> None:
        guild = MagicMock(id=123)
        track = make_track("track")
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=[track])
        player.enqueue_tracks = AsyncMock(return_value=None)
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(
                guild,
                MagicMock(),
                "query",
                1,
                2,
                placement="next",
            )

        player.enqueue_tracks.assert_awaited_once_with(
            (track,), TrackRequester(1, 2), placement="next"
        )
        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        data = result.data
        if data is None or isinstance(data, tuple):
            self.fail("Expected play response data")
        self.assertEqual(data["placement"], "next")

    async def test_play_next_playlist_enqueues_as_single_ordered_block(self) -> None:
        guild = MagicMock(id=123)
        tracks = [make_track("one"), make_track("two")]
        playlist = make_playlist("Mix", tracks)
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=playlist)
        player.enqueue_tracks = AsyncMock(return_value=None)
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(
                guild,
                MagicMock(),
                "query",
                1,
                2,
                placement="next",
            )

        player.enqueue_tracks.assert_awaited_once_with(
            tracks, TrackRequester(1, 2), placement="next"
        )
        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        data = result.data
        if data is None or isinstance(data, tuple):
            self.fail("Expected play response data")
        self.assertEqual(data["placement"], "next")

    async def test_play_end_playlist_keeps_end_placement(self) -> None:
        guild = MagicMock(id=123)
        tracks = [make_track("one"), make_track("two")]
        playlist = make_playlist("Mix", tracks)
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=playlist)
        player.enqueue_tracks = AsyncMock(return_value=None)
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(guild, MagicMock(), "query", 1, 2)

        player.enqueue_tracks.assert_awaited_once_with(
            tracks, TrackRequester(1, 2), placement="end"
        )
        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        data = result.data
        if data is None or isinstance(data, tuple):
            self.fail("Expected play response data")
        self.assertEqual(data["placement"], "end")

    async def test_play_empty_playlist_returns_nothing_found(self) -> None:
        guild = MagicMock(id=123)
        playlist = make_playlist("Empty", [])
        player = MagicMock()
        player.fetch_tracks = AsyncMock(return_value=playlist)
        player.enqueue_tracks = AsyncMock()
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        self.connection.get_player.return_value = player

        with patch.object(self.service, "join", join):
            result = await self.service.play(guild, MagicMock(), "query", 1, 2)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, "Nothing found")
        player.enqueue_tracks.assert_not_awaited()

    async def test_play_fetches_tracks_before_waiting_for_player_transition_lock(
        self,
    ) -> None:
        guild = MagicMock(id=123)
        track = make_track("track")
        transition_lock = asyncio.Lock()
        fetch_started = asyncio.Event()
        enqueue_started = asyncio.Event()
        join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        player = MagicMock()
        player._transition_lock = transition_lock

        async def fetch_tracks(_query: str) -> list[mafic.Track]:
            fetch_started.set()
            return [track]

        async def enqueue_tracks(
            tracks: tuple[mafic.Track, ...],
            requester: TrackRequester,
            *,
            placement: QueuePlacement,
        ) -> PlaybackAttempt | None:
            del tracks, requester, placement
            enqueue_started.set()
            async with transition_lock:
                return None

        player.fetch_tracks = AsyncMock(side_effect=fetch_tracks)
        player.enqueue_tracks = AsyncMock(side_effect=enqueue_tracks)
        self.connection.get_player.return_value = player

        await transition_lock.acquire()
        try:
            with patch.object(self.service, "join", join):
                play_task = asyncio.create_task(
                    self.service.play(guild, MagicMock(), "query", 1, 2)
                )
                await asyncio.wait_for(fetch_started.wait(), timeout=1.0)
                await asyncio.wait_for(enqueue_started.wait(), timeout=1.0)
                self.assertFalse(play_task.done())
                transition_lock.release()
                result = await play_task
        finally:
            if transition_lock.locked():
                transition_lock.release()

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        player.fetch_tracks.assert_awaited_once_with("query")

    def test_record_interaction_accepts_zero_ids(self) -> None:
        session = MagicMock()
        self.state.get_or_create_session.return_value = session

        self.service._record_interaction_if_possible(123, 0, 0)

        session.record_interaction.assert_called_once_with(0, 0)

    async def test_no_player_command_returns_unavailable_when_known_down(self) -> None:
        result = await self.service.pause(123)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)

    async def test_skip_uses_atomic_player_result_without_pre_reading_queue(
        self,
    ) -> None:
        before = make_entry("before")
        after = make_entry("after", entry_id=2)
        attempt = PlaybackAttempt(7, before)

        class PlayerStub:
            def __init__(self) -> None:
                self.skip = AsyncMock(return_value=(attempt, PlaybackAttempt(8, after)))
                self.resume = AsyncMock()

            @property
            def current_attempt(self) -> PlaybackAttempt:
                msg = "service must not pre-read current attempt"
                raise AssertionError(msg)

            @property
            def current(self) -> mafic.Track | None:
                msg = "service must not read current before skip"
                raise AssertionError(msg)

            @property
            def queue(self) -> object:
                msg = "service must not read queue before skip"
                raise AssertionError(msg)

        player = PlayerStub()
        session = MagicMock()
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False
        self.state.get_or_create_session.return_value = session
        self.ui.controller.destroy_for_guild = AsyncMock()

        result = await self.service.skip(123, requester_id=1, text_channel_id=2)

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        self.assertEqual(result.message, "Skipped")
        self.assertEqual(result.data, {"before": before.track, "after": after.track})
        player.skip.assert_awaited_once()
        player.resume.assert_not_awaited()
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.SKIP,
            expected_attempt_id=7,
        )
        session.record_interaction.assert_called_once_with(2, 1)

    async def test_skip_does_not_resume_when_no_track_started(self) -> None:
        before = make_entry("before")
        player = MagicMock()
        player.skip = AsyncMock(return_value=(PlaybackAttempt(7, before), None))
        player.resume = AsyncMock()
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock()

        result = await self.service.skip(123, requester_id=1, text_channel_id=2)

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        self.assertEqual(result.data, {"before": before.track, "after": None})
        player.skip.assert_awaited_once()
        player.resume.assert_not_awaited()

    async def test_skip_destroy_uses_attempt_returned_after_lock_wait(self) -> None:
        before_wait = PlaybackAttempt(1, make_entry("before-wait"))
        actually_skipped = PlaybackAttempt(2, make_entry("actually-skipped"))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def skip() -> tuple[PlaybackAttempt, None]:
            entered.set()
            await release.wait()
            return actually_skipped, None

        player = MagicMock(current_attempt=before_wait)
        player.skip = AsyncMock(side_effect=skip)
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock()

        service_skip = asyncio.create_task(self.service.skip(123))
        await entered.wait()
        player.current_attempt = actually_skipped
        release.set()
        result = await service_skip

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.SKIP,
            expected_attempt_id=actually_skipped.attempt_id,
        )

    async def test_rotate_uses_started_track_from_atomic_player_result(self) -> None:
        moved = make_entry("moved")
        started = make_entry("started", entry_id=2)
        player = MagicMock()
        player.rotate_current = AsyncMock(
            return_value=(PlaybackAttempt(7, moved), PlaybackAttempt(8, started))
        )
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False

        result = await self.service.rotate(123, requester_id=1, text_channel_id=2)

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        self.assertEqual(result.data, {"skipped": moved.track, "next": started.track})
        player.rotate_current.assert_awaited_once()

    async def test_stop_uses_atomic_stop_and_clear(self) -> None:
        calls: list[str] = []
        player = MagicMock()

        async def stop_and_clear() -> None:
            calls.append("stop")

        async def destroy_for_guild(*_args: object) -> None:
            calls.append("destroy")

        player.stop_and_clear = AsyncMock(side_effect=stop_and_clear)
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock(side_effect=destroy_for_guild)

        result = await self.service.stop(123, requester_id=1, text_channel_id=2)

        self.assertIs(result.status, MusicResultStatus.SUCCESS)
        self.assertEqual(calls, ["destroy", "stop"])
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.MANUAL_STOP,
        )
        player.stop_and_clear.assert_awaited_once()
        player.clear_queue.assert_not_called()
        player.stop.assert_not_called()

    async def test_stop_io_error_happens_after_controller_destroy(self) -> None:
        player = MagicMock()
        error = mafic.HTTPNotFound("Session not found")
        player.stop_and_clear = AsyncMock(side_effect=error)
        self.connection.get_player.return_value = player
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock()

        with patch.object(
            self.service,
            "_handle_player_io_failure",
            new=AsyncMock(return_value=MusicResult(MusicResultStatus.FAILURE, "down")),
        ) as handle_failure:
            result = await self.service.stop(123, requester_id=1, text_channel_id=2)

        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.ui.controller.destroy_for_guild.assert_awaited_once_with(
            123,
            ControllerDestroyReason.MANUAL_STOP,
        )
        player.stop_and_clear.assert_awaited_once()
        handle_failure.assert_awaited_once_with(player, error)

    async def test_leave_stale_voice_returns_unavailable_after_local_cleanup(
        self,
    ) -> None:
        guild = MagicMock()
        guild.id = 123
        guild.voice_client = object()
        self.connection.get_player.return_value = None
        self.connection.disconnect = AsyncMock()
        self.connection.is_known_unavailable.return_value = False
        self.ui.controller.destroy_for_guild = AsyncMock()
        end_session = AsyncMock()

        with (
            patch("api.music.service.core_service.mafic.Player", object),
            patch.object(self.service, "end_session", end_session),
        ):
            result = await self.service.leave(guild)

        self.connection.disconnect.assert_awaited_once_with(guild, force=True)
        self.assertIs(result.status, MusicResultStatus.FAILURE)
        self.assertEqual(result.message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)

    async def test_join_returns_unavailable_when_apply_volume_http_not_found(
        self,
    ) -> None:
        await self._assert_apply_volume_error_is_soft_failure(
            mafic.HTTPNotFound("Session not found")
        )

    async def test_join_returns_unavailable_when_apply_volume_client_error(
        self,
    ) -> None:
        await self._assert_apply_volume_error_is_soft_failure(
            aiohttp.ClientConnectionError("down")
        )
