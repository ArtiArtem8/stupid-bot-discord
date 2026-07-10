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
from tests.api.music.helpers import make_playlist, make_track


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.display_name = "Requester"
    interaction.user.display_avatar.url = "https://example.com/avatar.png"
    interaction.channel_id = 777
    return interaction


class TestMusicCogPlay(unittest.IsolatedAsyncioTestCase):
    async def test_play_command_passes_end_placement(self) -> None:
        cog = MusicCog.__new__(MusicCog)
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cast(Any, MusicCog.play).callback(cog, interaction, "query")

        run_play.assert_awaited_once_with(interaction, "query", "end")

    async def test_play_next_command_passes_next_placement(self) -> None:
        cog = MusicCog.__new__(MusicCog)
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cast(Any, MusicCog.play_next).callback(cog, interaction, "query")

        run_play.assert_awaited_once_with(interaction, "query", "next")

    async def test_run_play_command_calls_service_with_requested_placement(
        self,
    ) -> None:
        cog = MusicCog.__new__(MusicCog)
        guild = MagicMock(id=123)
        channel = MagicMock()
        interaction = _make_interaction()
        track = make_track("one")
        data: TrackResponseData = {
            "type": "track",
            "track": track,
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
        cog = MusicCog.__new__(MusicCog)
        interaction = _make_interaction()
        track = make_track("one")
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
                    "placement": placement,
                }

                embed = cog._build_track_embed(interaction, data)

                self.assertEqual(embed.title, expected_title)

    def test_playlist_embed_titles_follow_placement(self) -> None:
        cog = MusicCog.__new__(MusicCog)
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
                    "placement": placement,
                }

                embed = cog._build_playlist_embed(interaction, data)

                self.assertEqual(embed.title, expected_title)
