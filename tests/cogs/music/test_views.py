"""Tests for track controller lifecycle and component acknowledgement."""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from discord import Client, Interaction, ui

from api.music.models import ControllerDestroyReason, TrackId
from cogs.music.views import TrackControllerManager, TrackControllerView


class TestTrackControllerManager(unittest.IsolatedAsyncioTestCase):
    async def test_old_track_destroy_does_not_remove_new_controller(self) -> None:
        manager = TrackControllerManager(MagicMock())
        current_view = MagicMock(track_id=TrackId("new"))
        manager.controllers[1] = current_view
        cleanup_existing = AsyncMock()

        with patch.object(manager, "_cleanup_existing", cleanup_existing):
            await manager.destroy_for_guild(
                1,
                ControllerDestroyReason.TRACK_END,
                expected_track_id=TrackId("old"),
            )

        cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)

    async def test_create_for_user_replaces_existing_controller_and_message(
        self,
    ) -> None:
        manager = TrackControllerManager(MagicMock())
        old_view = MagicMock()
        manager.controllers[1] = old_view
        manager._active_messages[1] = (10, 20)

        track = MagicMock(identifier="new-track")
        new_player = MagicMock(current=track)
        message = MagicMock(id=21)
        message.channel.id = 10
        channel = MagicMock()
        channel.send = AsyncMock(return_value=message)
        new_view = MagicMock()
        safe_delete_message = AsyncMock()

        with (
            patch.object(manager, "_wait_for_sync", new=AsyncMock(return_value=True)),
            patch.object(manager, "_safe_delete_message", safe_delete_message),
            patch("cogs.music.views.TrackControllerView", return_value=new_view),
        ):
            await manager.create_for_user(
                guild_id=1,
                user_id=2,
                channel=channel,
                player=new_player,
                track=track,
            )

        old_view.stop.assert_called_once()
        safe_delete_message.assert_awaited_once_with(10, 20)
        self.assertEqual(manager.controllers, {1: new_view})
        self.assertEqual(manager._active_messages, {1: (10, 21)})


class TestTrackControllerView(unittest.IsolatedAsyncioTestCase):
    async def test_restart_acknowledges_before_player_seek(self) -> None:
        calls: list[str] = []
        track = MagicMock(identifier="track")
        player = MagicMock(current=track, paused=False)

        async def seek(_position: int) -> None:
            calls.append("seek")

        player.seek = AsyncMock(side_effect=seek)
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            track_id=TrackId("track"),
            on_stop_callback=None,
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
        track = MagicMock(identifier="track")
        player = MagicMock(current=track, paused=False)
        player.pause = AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            track_id=TrackId("track"),
            on_stop_callback=on_stop,
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

        player.cleanup.assert_called_once()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()
