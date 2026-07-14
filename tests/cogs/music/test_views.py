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


class TestTrackControllerView(unittest.IsolatedAsyncioTestCase):
    async def test_skip_calls_player_and_stops_controller_silently(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt)
        player.skip = AsyncMock()
        on_stop = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt_id=1,
            on_stop_callback=on_stop,
            on_player_failure=AsyncMock(),
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
            skip_button = next(
                child
                for child in view.children
                if isinstance(child, ui.Button) and child.custom_id == "btn_skip"
            )
            callback = skip_button.callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        acknowledge.assert_awaited_once()
        player.skip.assert_awaited_once_with()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.SKIP)
        send_warning.assert_not_awaited()
        self.assertTrue(view.is_finished())

    async def test_restart_acknowledges_before_player_seek(self) -> None:
        calls: list[str] = []
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)

        async def seek(_position: int) -> None:
            calls.append("seek")

        player.seek = AsyncMock(side_effect=seek)
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt_id=1,
            on_stop_callback=None,
            on_player_failure=AsyncMock(),
        )
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
            restart_button = next(
                child
                for child in view.children
                if isinstance(child, ui.Button) and child.custom_id == "btn_restart"
            )
            callback = restart_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        self.assertEqual(calls, ["ack", "seek"])

    async def test_pause_resume_handles_lavalink_io_error(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)
        player.pause = AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt_id=1,
            on_stop_callback=on_stop,
            on_player_failure=on_player_failure,
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
            pause_button = next(
                child
                for child in view.children
                if isinstance(child, ui.Button)
                and child.custom_id == "btn_pause_resume"
            )
            callback = pause_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        on_player_failure.assert_awaited_once_with(player)
        player.cleanup.assert_not_called()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()

    async def test_invalidation_failure_still_stops_and_warns(self) -> None:
        player = MagicMock()
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock(side_effect=RuntimeError("unexpected"))
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt_id=1,
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
