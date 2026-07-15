"""Tests for track controller lifecycle and component acknowledgement."""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from discord import Client, Interaction, ui

from api.music.models import ControllerDestroyReason, PlaybackAttempt
from cogs.music.views import TrackControllerManager, TrackControllerView
from tests.api.music.helpers import make_entry


class TestTrackControllerManager(unittest.IsolatedAsyncioTestCase):
    async def test_stale_view_stop_does_not_remove_new_controller(self) -> None:
        manager = TrackControllerManager(MagicMock(), MagicMock())
        stale_view = MagicMock()
        current_view = MagicMock()
        manager.controllers[1] = current_view
        cleanup_existing = AsyncMock()

        with patch.object(manager, "_cleanup_existing", cleanup_existing):
            await manager.destroy_for_guild(
                1,
                ControllerDestroyReason.STALE_VIEW,
                requesting_view=stale_view,
            )

        cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)

    async def test_old_track_destroy_does_not_remove_new_controller(self) -> None:
        manager = TrackControllerManager(MagicMock(), MagicMock())
        current_view = MagicMock(attempt_id=2)
        manager.controllers[1] = current_view
        cleanup_existing = AsyncMock()

        with patch.object(manager, "_cleanup_existing", cleanup_existing):
            await manager.destroy_for_guild(
                1,
                ControllerDestroyReason.TRACK_END,
                expected_attempt_id=1,
            )

        cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)

    async def test_create_for_user_replaces_existing_controller_and_message(
        self,
    ) -> None:
        connection = MagicMock()
        connection.invalidate_player = AsyncMock()
        manager = TrackControllerManager(MagicMock(), connection)
        old_view = MagicMock()
        manager.controllers[1] = old_view
        manager._active_messages[1] = (10, 20)

        attempt = PlaybackAttempt(2, make_entry("new-track"))
        new_player = MagicMock(current_attempt=attempt)
        message = MagicMock(id=21)
        message.channel.id = 10
        channel = MagicMock()
        channel.send = AsyncMock(return_value=message)
        new_view = MagicMock()
        safe_delete_message = AsyncMock()

        with (
            patch.object(manager, "_safe_delete_message", safe_delete_message),
            patch(
                "cogs.music.views.TrackControllerView", return_value=new_view
            ) as view_cls,
        ):
            await manager.create_for_user(
                guild_id=1,
                user_id=2,
                channel=channel,
                player=new_player,
                attempt=attempt,
            )

        old_view.stop.assert_called_once()
        safe_delete_message.assert_awaited_once_with(10, 20)
        self.assertEqual(manager.controllers, {1: new_view})
        self.assertEqual(manager._active_messages, {1: (10, 21)})
        self.assertEqual(
            view_cls.call_args.kwargs["on_player_failure"],
            connection.invalidate_player,
        )
        self.assertIs(view_cls.call_args.kwargs["attempt"], attempt)


class TestTrackControllerView(unittest.IsolatedAsyncioTestCase):
    def _make_view(
        self,
        attempt: PlaybackAttempt,
        player: MagicMock | None = None,
        *,
        on_stop: AsyncMock | None = None,
    ) -> tuple[TrackControllerView, MagicMock, AsyncMock]:
        if player is None:
            player = MagicMock(current_attempt=attempt, paused=False, position=0)
        player.seek_attempt = AsyncMock(return_value=True)
        player.toggle_pause_for_attempt = AsyncMock(return_value=True)
        player.skip = AsyncMock(return_value=(attempt, None))
        stop_callback = on_stop or AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=stop_callback,
            on_player_failure=AsyncMock(),
        )
        return view, player, stop_callback

    def _button(
        self, view: TrackControllerView, custom_id: str
    ) -> ui.Item[TrackControllerView]:
        button = next(
            child
            for child in view.children
            if isinstance(child, ui.Button) and child.custom_id == custom_id
        )
        return button

    def test_view_stores_exact_playback_attempt(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, _, _ = self._make_view(attempt)

        self.assertIs(view.attempt, attempt)
        self.assertEqual(view.attempt_id, attempt.attempt_id)

    async def test_skip_calls_player_and_stops_controller_silently(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        on_stop = AsyncMock()
        view, player, _ = self._make_view(
            attempt,
            MagicMock(current_attempt=attempt),
            on_stop=on_stop,
        )
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ) as acknowledge,
            patch("cogs.music.views.send_warning", new=AsyncMock()) as send_warning,
        ):
            skip_button = self._button(view, "btn_skip")
            callback = skip_button.callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        acknowledge.assert_awaited_once()
        player.skip.assert_awaited_once_with(expected=attempt)
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.SKIP)
        send_warning.assert_not_awaited()
        self.assertTrue(view.is_finished())

    async def test_restart_acknowledges_before_player_seek(self) -> None:
        calls: list[str] = []
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)

        async def seek(_attempt: PlaybackAttempt, _position: int) -> bool:
            calls.append("seek")
            return True

        view, player, _ = self._make_view(attempt, player)
        player.seek_attempt = AsyncMock(side_effect=seek)
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        async def acknowledge(_responder: object) -> None:
            calls.append("ack")

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=acknowledge,
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            restart_button = self._button(view, "btn_restart")
            callback = restart_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        self.assertEqual(calls, ["ack", "seek"])
        player.seek_attempt.assert_awaited_once_with(attempt, 0)

    async def test_seek_buttons_pass_exact_attempt_and_expected_positions(
        self,
    ) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        attempt.entry.track.length = 25_000
        cases = (
            ("btn_restart", 0),
            ("btn_back10", 5_000),
            ("btn_fwd10", 25_000),
        )

        for custom_id, expected_position in cases:
            with self.subTest(custom_id=custom_id):
                player = MagicMock(
                    current_attempt=attempt,
                    current=MagicMock(length=999_000),
                    paused=False,
                    position=15_000,
                )
                view, player, _ = self._make_view(attempt, player)
                safe_update = AsyncMock()
                interaction = MagicMock()
                interaction.user.id = 10

                with (
                    patch(
                        "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                        new=AsyncMock(),
                    ),
                    patch.object(view, "_safe_update", safe_update),
                ):
                    callback = self._button(view, custom_id).callback
                    self.assertIsNotNone(callback)
                    await callback(interaction)

                player.seek_attempt.assert_awaited_once_with(attempt, expected_position)
                safe_update.assert_awaited_once_with(force=True)

    async def test_rejected_seek_preserves_cache_and_does_not_update(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=True, position=15_000)
        view, player, on_stop = self._make_view(attempt, player)
        player.seek_attempt.return_value = False
        view._is_paused_cache = True
        view._frozen_position = 15_000
        view._pause_start_time = 123.0
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            callback = self._button(view, "btn_back10").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        self.assertEqual(view._frozen_position, 15_000)
        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._pause_start_time, 123.0)
        safe_update.assert_not_awaited()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)
        player.seek.assert_not_called()

    async def test_pause_resume_handles_lavalink_io_error(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=on_stop,
            on_player_failure=on_player_failure,
        )
        player.toggle_pause_for_attempt = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("down")
        )
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ),
            patch("cogs.music.views.send_warning", new=AsyncMock()) as send_warning,
        ):
            pause_button = self._button(view, "btn_pause_resume")
            callback = pause_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        on_player_failure.assert_awaited_once_with(player)
        player.cleanup.assert_not_called()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()

    async def test_pause_resume_updates_cache_from_guarded_result(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False, position=12_000)
        view, player, _ = self._make_view(attempt, player)
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
            patch("cogs.music.views.time.monotonic", return_value=50.0),
        ):
            callback = self._button(view, "btn_pause_resume").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        player.toggle_pause_for_attempt.assert_awaited_once_with(attempt)
        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._frozen_position, 12_000)
        self.assertEqual(view._pause_start_time, 50.0)
        safe_update.assert_awaited_once_with(force=True)
        player.pause.assert_not_called()
        player.resume.assert_not_called()

        player.toggle_pause_for_attempt.reset_mock(return_value=True)
        player.toggle_pause_for_attempt.return_value = False
        safe_update.reset_mock()

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            await callback(interaction)

        self.assertFalse(view._is_paused_cache)
        self.assertIsNone(view._pause_start_time)
        safe_update.assert_awaited_once_with(force=True)

    async def test_rejected_pause_preserves_cache_and_does_not_update(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False, position=20_000)
        view, player, on_stop = self._make_view(attempt, player)
        player.toggle_pause_for_attempt.return_value = None
        view._is_paused_cache = True
        view._frozen_position = 8_000
        view._pause_start_time = 10.0
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.MusicInteractionResponder.acknowledge_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            callback = self._button(view, "btn_pause_resume").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._frozen_position, 8_000)
        self.assertEqual(view._pause_start_time, 10.0)
        safe_update.assert_not_awaited()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)
        player.pause.assert_not_called()
        player.resume.assert_not_called()

    async def test_rejected_skip_stops_controller_as_stale(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, player, on_stop = self._make_view(attempt)
        player.skip.return_value = (None, None)
        interaction = MagicMock()
        interaction.user.id = 10

        with patch(
            "cogs.music.views.MusicInteractionResponder.acknowledge_component",
            new=AsyncMock(),
        ):
            callback = self._button(view, "btn_skip").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        player.skip.assert_awaited_once_with(expected=attempt)
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)

    async def test_invalidation_failure_still_stops_and_warns(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock()
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock(side_effect=RuntimeError("unexpected"))
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=on_stop,
            on_player_failure=on_player_failure,
        )
        interaction = MagicMock()

        with (
            patch("cogs.music.views.logger.exception") as log_exception,
            patch("cogs.music.views.send_warning", new=AsyncMock()) as send_warning,
        ):
            await view.handle_player_io_error(interaction)

        on_player_failure.assert_awaited_once_with(player)
        log_exception.assert_called_once()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()
        player.cleanup.assert_not_called()
