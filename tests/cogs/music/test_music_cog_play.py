"""Tests for music play command placement behavior."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from api.music.models import (
    MusicResult,
    MusicResultStatus,
    PlaylistResponseData,
    PlayPlacement,
    TrackResponseData,
)
from cogs.music.music_cog import MusicCog
from cogs.music.presentation import (
    build_playlist_added_embed,
    build_track_added_embed,
)
from cogs.music.views import QueueUndoView
from framework import FeedbackUI
from tests.api.music.helpers import make_entry, make_playlist, make_track


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.display_name = "Requester"
    interaction.user.display_avatar.url = "https://example.com/avatar.png"
    interaction.channel_id = 777
    interaction.guild_id = 123
    return interaction


def _make_cog() -> MusicCog:
    cog = object.__new__(MusicCog)
    return cog


class TestMusicCogPlay(unittest.IsolatedAsyncioTestCase):
    async def test_play_command_passes_end_placement(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cast(Any, MusicCog.play).callback(cog, interaction, "query")

        run_play.assert_awaited_once_with(interaction, "query", "end")

    async def test_play_next_command_passes_next_placement(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cast(Any, MusicCog.play_next).callback(cog, interaction, "query")

        run_play.assert_awaited_once_with(interaction, "query", "next")

    async def test_run_play_command_calls_service_with_requested_placement(
        self,
    ) -> None:
        cog = _make_cog()
        guild = MagicMock(id=123)
        channel = MagicMock()
        interaction = _make_interaction()
        entry = make_entry("one", requester_id=42)
        track = entry.track
        data: TrackResponseData = {
            "type": "track",
            "track": track,
            "undo_entries": (entry,),
            "placement": "next",
        }
        result = MusicResult(MusicResultStatus.SUCCESS, "ok", data=data)
        service = MagicMock()
        service.play = AsyncMock(return_value=result)
        service.get_queue_duration = AsyncMock(return_value=track.length)
        cog.service = service

        async def wait_for_operation(
            responder: object,
            operation: object,
            *,
            ephemeral: bool = False,
        ) -> MusicResult[object]:
            del responder, ephemeral
            return await cast(Any, operation)

        with (
            patch.object(cog, "_require_guild", new=AsyncMock(return_value=guild)),
            patch.object(
                cog,
                "_get_voice_channel_for_play",
                new=AsyncMock(return_value=channel),
            ),
            patch.object(
                cog,
                "_resolve_play_response_data",
                new=AsyncMock(return_value=data),
            ),
            patch.object(cog, "_send_play_feedback", new=AsyncMock()) as send_feedback,
            patch(
                "cogs.music.music_cog.MusicInteractionResponder.await_with_defer_budget",
                autospec=True,
                side_effect=wait_for_operation,
            ),
        ):
            await cog._run_play_command(interaction, " query ", "next")

        service.play.assert_awaited_once_with(
            guild,
            channel,
            "query",
            interaction.user.id,
            interaction.channel_id,
            placement="next",
        )
        service.get_queue_duration.assert_awaited_once_with(guild.id)
        send_feedback.assert_awaited_once()

    def test_track_embed_titles_follow_placement(self) -> None:
        interaction = _make_interaction()
        entry = make_entry("one", requester_id=42)
        track = entry.track
        cases: dict[PlayPlacement, str] = {
            "now": "Сейчас играет",
            "next": "Добавлено в начало очереди",
            "end": "Добавлено в очередь",
        }

        for placement, expected_title in cases.items():
            with self.subTest(placement=placement):
                data: TrackResponseData = {
                    "type": "track",
                    "track": track,
                    "undo_entries": (entry,),
                    "placement": placement,
                }

                embed = build_track_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                )

                self.assertEqual(embed.title, expected_title)

    async def test_queued_track_feedback_has_undo_for_end_and_next(self) -> None:
        interaction = _make_interaction()
        entry = make_entry("one", requester_id=42)
        cog = _make_cog()
        cog.service = MagicMock()
        cog.service.remove_queued_entries = AsyncMock()

        for placement in ("end", "next"):
            with self.subTest(placement=placement):
                data: TrackResponseData = {
                    "type": "track",
                    "track": entry.track,
                    "undo_entries": (entry,),
                    "placement": placement,
                }
                with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
                    await cog._send_play_feedback(interaction, data, 120)

                send.assert_awaited_once()
                send_call = send.await_args
                if send_call is None:
                    self.fail("Expected queued-track feedback")
                view = send_call.kwargs["view"]
                self.assertIsInstance(view, QueueUndoView)
                self.assertEqual(view.remove_button.label, "Удалить")
                self.assertEqual(view.expected_entries, (entry,))
                self.assertEqual(view.timeout, 120)

    async def test_immediate_track_feedback_has_no_undo_view(self) -> None:
        interaction = _make_interaction()
        entry = make_entry("one", requester_id=42)
        data: TrackResponseData = {
            "type": "track",
            "track": entry.track,
            "undo_entries": (),
            "placement": "now",
        }
        cog = _make_cog()

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
            await cog._send_play_feedback(interaction, data, 120)

        send_call = send.await_args
        if send_call is None:
            self.fail("Expected immediate-track feedback")
        self.assertIsNone(send_call.kwargs["view"])

    async def test_single_track_playlist_now_has_no_undo_view(self) -> None:
        interaction = _make_interaction()
        entry = make_entry("one", requester_id=42)
        playlist = make_playlist("Mix", [entry.track])
        data: PlaylistResponseData = {
            "type": "playlist",
            "playlist": playlist,
            "undo_entries": (),
            "placement": "now",
        }
        cog = _make_cog()

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
            await cog._send_play_feedback(interaction, data, 120)

        send_call = send.await_args
        if send_call is None:
            self.fail("Expected playlist feedback")
        self.assertIsNone(send_call.kwargs["view"])

    async def test_multi_track_playlist_now_has_undo_for_all_entries(self) -> None:
        interaction = _make_interaction()
        entries = (
            make_entry("one", entry_id=1, requester_id=42),
            make_entry("two", entry_id=2, requester_id=42),
        )
        playlist = make_playlist("Mix", [entry.track for entry in entries])
        data: PlaylistResponseData = {
            "type": "playlist",
            "playlist": playlist,
            "undo_entries": entries,
            "placement": "now",
        }
        cog = _make_cog()
        cog.service = MagicMock()
        cog.service.remove_queued_entries = AsyncMock()

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
            await cog._send_play_feedback(interaction, data, 120)

        send_call = send.await_args
        if send_call is None:
            self.fail("Expected playlist feedback")
        view = send_call.kwargs["view"]
        self.assertIsInstance(view, QueueUndoView)
        self.assertIs(view.expected_entries, entries)

    async def test_queued_playlist_has_undo_for_end_and_next(self) -> None:
        interaction = _make_interaction()
        entries = (
            make_entry("one", entry_id=1, requester_id=42),
            make_entry("two", entry_id=2, requester_id=42),
        )
        playlist = make_playlist("Mix", [entry.track for entry in entries])
        cog = _make_cog()
        cog.service = MagicMock()
        cog.service.remove_queued_entries = AsyncMock()

        for placement in ("end", "next"):
            with self.subTest(placement=placement):
                data: PlaylistResponseData = {
                    "type": "playlist",
                    "playlist": playlist,
                    "undo_entries": entries,
                    "placement": placement,
                }
                with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
                    await cog._send_play_feedback(interaction, data, 120)

                send_call = send.await_args
                if send_call is None:
                    self.fail("Expected queued playlist feedback")
                view = send_call.kwargs["view"]
                self.assertIsInstance(view, QueueUndoView)
                self.assertIs(view.expected_entries, entries)

    def test_playlist_embed_titles_follow_placement(self) -> None:
        interaction = _make_interaction()
        playlist = make_playlist("Mix", [make_track("one"), make_track("two")])
        cases: dict[PlayPlacement, str] = {
            "now": "Плейлист запущен",
            "next": "Плейлист добавлен в начало очереди",
            "end": "Добавлен плейлист **Mix**",
        }

        for placement, expected_title in cases.items():
            with self.subTest(placement=placement):
                data: PlaylistResponseData = {
                    "type": "playlist",
                    "playlist": playlist,
                    "undo_entries": (),
                    "placement": placement,
                }

                embed = build_playlist_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                )

                self.assertEqual(embed.title, expected_title)
