"""Tests for music play command placement behavior."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.music.discord import music_cog
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.discord.presentation import (
    build_playlist_added_embed,
    build_track_added_embed,
)
from stupidbot.features.music.discord.views import QueueUndoView
from stupidbot.features.music.playback.models import PlayPlacement
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.results import (
    MusicResult,
    MusicResultReason,
    MusicResultStatus,
    PlaylistResponseData,
    TrackResponseData,
    VoiceCheckResult,
)
from stupidbot.features.preferences.models import Preferences
from tests.features.music.fixtures import make_entry, make_playlist, make_track


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.client = MagicMock()
    interaction.client.block_manager = MagicMock()
    interaction.client.block_manager.is_user_blocked = AsyncMock(return_value=False)
    interaction.user.id = 42
    interaction.user.display_name = "Requester"
    interaction.user.display_avatar.url = "https://example.com/avatar.png"
    interaction.channel_id = 777
    interaction.guild_id = 123
    return interaction


def _make_cog() -> MusicCog:
    with patch.object(music_cog, "create_music_components"):
        return MusicCog(MagicMock(spec=commands.Bot), MagicMock(spec=VolumeStore))


class TestMusicCogPlay(unittest.IsolatedAsyncioTestCase):
    async def test_registered_operations_acknowledge_with_command_visibility(
        self,
    ) -> None:
        bot = StupidBot()
        self.addAsyncCleanup(bot.close)
        cog = _make_cog()
        service = MagicMock()
        service.voice_lifecycle.is_healing.return_value = False
        service.join = AsyncMock(return_value=(VoiceCheckResult.SUCCESS, None))
        service.leave = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "Disconnected",
                reason=MusicResultReason.DISCONNECTED,
            )
        )
        service.play = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.FAILURE,
                "Nothing found",
                reason=MusicResultReason.NOTHING_FOUND,
            )
        )
        cog.service = service
        channel = MagicMock(spec=discord.VoiceChannel, id=20)
        item = _make_interaction()
        item._state = bot._connection
        item.client = bot
        item.guild = MagicMock(
            spec=discord.Guild, id=123, voice_client=MagicMock(channel=channel)
        )
        item.user = MagicMock(spec=discord.Member, id=42)
        item.user.voice = MagicMock(channel=channel)
        item.user.guild_permissions = discord.Permissions.none()
        item.type = discord.InteractionType.application_command
        item.created_at = datetime.now(UTC)
        self.enterContext(patch.object(bot, "dispatch", MagicMock()))
        self.enterContext(
            patch.object(bot.block_manager, "is_user_blocked", return_value=False)
        )
        self.enterContext(
            patch.object(bot.preferences_repository, "get", return_value=Preferences())
        )
        for command, operation, private in (
            (cog.join, service.join, True),
            (cog.leave, service.leave, True),
            (cog.play, service.play, False),
            (cog.play_next, service.play, False),
        ):
            with self.subTest(command=command.name):
                bot.tree.add_command(command)
                item.command = command
                item.command_failed = False
                item.extras = {}
                item.data = {
                    "name": command.name,
                    "type": 1,
                    "options": [{"name": "query", "type": 3, "value": "track"}]
                    if command.name.startswith("play")
                    else [],
                }
                item.response = MagicMock(spec=discord.InteractionResponse)
                item.response.is_done.return_value = False
                item.response.type = None
                item.edit_original_response = AsyncMock()

                async def defer(
                    *,
                    thinking: bool,
                    ephemeral: bool,
                    expected_private: bool = private,
                    expected_operation: AsyncMock = operation,
                ) -> None:
                    self.assertTrue(thinking)
                    self.assertEqual(ephemeral, expected_private)
                    expected_operation.assert_not_awaited()
                    item.response.is_done.return_value = True
                    item.response.type = (
                        discord.InteractionResponseType.deferred_channel_message
                    )

                operation.reset_mock()
                item.response.defer.side_effect = defer
                await bot.tree._call(item)

                self.assertFalse(item.command_failed)
                operation.assert_awaited_once()
                item.response.defer.assert_awaited_once_with(
                    thinking=True, ephemeral=private
                )
                item.response.send_message.assert_not_awaited()
                item.edit_original_response.assert_awaited_once()

    async def test_play_command_passes_end_placement(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cog.play._do_call(interaction, {"query": "query"})

        run_play.assert_awaited_once_with(interaction, "query", "end")

    async def test_play_next_command_passes_next_placement(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()

        with patch.object(cog, "_run_play_command", new=AsyncMock()) as run_play:
            await cog.play_next._do_call(interaction, {"query": "query"})

        run_play.assert_awaited_once_with(interaction, "query", "next")

    async def test_empty_query_warns_without_starting_operation(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()
        service = MagicMock()
        service.play = AsyncMock()
        cog.service = service

        with (
            patch.object(
                cog,
                "_require_guild",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "stupidbot.features.music.discord.music_cog.send_warning",
                new=AsyncMock(),
            ) as send_warning,
        ):
            await cog._run_play_command(interaction, "   ", "end")

        send_warning.assert_awaited_once_with(
            interaction,
            "Укажите название или ссылку на трек.",
            ephemeral=True,
        )
        service.play.assert_not_called()

    async def test_missing_voice_warns_without_starting_operation(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()
        service = MagicMock()
        service.play = AsyncMock()
        cog.service = service

        with (
            patch.object(
                cog,
                "_require_guild",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch.object(
                cog,
                "_get_voice_channel_for_play",
                new=AsyncMock(return_value=None),
            ),
        ):
            await cog._run_play_command(interaction, "query", "next")

        service.play.assert_not_called()

    async def test_voice_preflight_warning_is_private(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()
        interaction.user = MagicMock()
        interaction.user.voice = None

        with patch(
            "stupidbot.features.music.discord.music_cog.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            channel = await cog._get_voice_channel_for_play(interaction)

        self.assertIsNone(channel)
        send_warning.assert_awaited_once_with(
            interaction,
            "Вы должны быть в голосовом канале!",
            ephemeral=True,
        )

    async def test_run_play_command_calls_service_with_requested_placement(
        self,
    ) -> None:
        cog = _make_cog()
        guild = MagicMock(id=123)
        channel = MagicMock(spec=discord.VoiceChannel, id=20)
        interaction = _make_interaction()
        interaction.command = cog.play_next
        interaction.guild = guild
        guild.voice_client = MagicMock(channel=channel)
        interaction.user = MagicMock(
            spec=discord.Member, id=42, voice=MagicMock(channel=channel)
        )
        entry = make_entry("one", requester_id=42)
        track = entry.track
        data: TrackResponseData = {
            "type": "track",
            "track": track,
            "undo_entries": (entry,),
            "placement": "next",
        }
        result = MusicResult(
            MusicResultStatus.SUCCESS,
            "ok",
            data=data,
            reason=MusicResultReason.TRACK_PROCESSED,
        )
        service = MagicMock()
        service.play = AsyncMock(return_value=result)
        service.get_queue_duration = AsyncMock(return_value=track.length)
        cog.service = service

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

    async def test_run_play_command_calls_service_with_end_placement(self) -> None:
        cog = _make_cog()
        interaction = _make_interaction()
        guild = MagicMock()
        channel = MagicMock(spec=discord.VoiceChannel, id=20)
        interaction.command = cog.play
        interaction.guild = guild
        guild.voice_client = MagicMock(channel=channel)
        interaction.user = MagicMock(
            spec=discord.Member, id=42, voice=MagicMock(channel=channel)
        )
        result: MusicResult[object] = MusicResult(
            MusicResultStatus.FAILURE,
            "Nothing found",
            reason=MusicResultReason.NOTHING_FOUND,
        )
        service = MagicMock()
        service.play = AsyncMock(return_value=result)
        cog.service = service

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
                new=AsyncMock(return_value=None),
            ),
        ):
            await cog._run_play_command(interaction, "query", "end")

        service.play.assert_awaited_once_with(
            guild,
            channel,
            "query",
            interaction.user.id,
            interaction.channel_id,
            placement="end",
        )

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
