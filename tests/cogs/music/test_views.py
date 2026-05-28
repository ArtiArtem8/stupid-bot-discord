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
        manager._cleanup_existing = AsyncMock()  # type: ignore[method-assign]

        await manager.destroy_for_guild(
            1,
            ControllerDestroyReason.TRACK_END,
            expected_track_id=TrackId("old"),
        )

        manager._cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)


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
        view._safe_update = AsyncMock()  # type: ignore[method-assign]
        interaction = MagicMock()
        interaction.user.id = 10

        async def acknowledge(_responder: object) -> None:
            calls.append("ack")

        with patch(
            "cogs.music.views.MusicInteractionResponder.acknowledge_component",
            new=acknowledge,
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
