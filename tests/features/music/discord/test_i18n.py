"""Music presentation keeps public snapshots separate from private feedback."""

import unittest
from copy import copy
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.music.contracts import TrackExceptionPayload
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.discord.presentation import (
    build_session_summary_embed,
    build_track_added_embed,
)
from stupidbot.features.music.discord.views import QueuePaginationAdapter
from stupidbot.features.music.messages import MusicMessages
from stupidbot.features.music.playback.models import (
    QueueSnapshot,
    RepeatMode,
)
from stupidbot.features.music.results import (
    MusicResult,
    MusicResultReason,
    MusicResultStatus,
)
from stupidbot.features.music.runtime.session import MusicSession, TrackInfo
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.music.fixtures import make_track
from tests.features.preferences.fixtures import interaction


class TestMusicTranslations(unittest.IsolatedAsyncioTestCase):
    def test_day_plural_forms(self) -> None:
        ru = MusicMessages(catalog("music", Language.RU))
        en = MusicMessages(catalog("music", Language.EN))
        for count, ending in (
            (0, "дней"),
            (1, "день"),
            (2, "дня"),
            (11, "дней"),
            (21, "день"),
        ):
            with self.subTest(count=count):
                self.assertEqual(ru.days(count=count), f"{count} {ending}")
                self.assertEqual(
                    en.days(count=count), f"{count} day" + ("" if count == 1 else "s")
                )

    def test_track_content_and_requester_are_not_translated(self) -> None:
        track = make_track("Доброе утро")
        embed = build_track_added_embed(
            {
                "type": "track",
                "track": track,
                "undo_entries": (),
                "placement": "now",
            },
            requester_name="Иван",
            requester_avatar_url="https://example.com/avatar.png",
            language=Language.EN,
        )
        self.assertEqual(embed.title, "Now playing")
        self.assertIn(track.title, embed.description or "")
        self.assertEqual(embed.footer.text, "Requested by: Иван")

    def test_refresh_keeps_queue_language(self) -> None:
        adapter = QueuePaginationAdapter(
            QueueSnapshot(None, (), RepeatMode.OFF),
            context=PresentationContext(Language.EN, ZoneInfo("UTC")),
        )
        adapter.update_snapshot(QueueSnapshot(None, (), RepeatMode.OFF))
        self.assertEqual(adapter.make_embed(0).title, "Playback queue")
        self.assertEqual(
            adapter.make_embed(0).footer.text, "Page 1/1 • Queued: 0 • Repeat: off"
        )

    def test_queue_repeat_modes_follow_the_panel_language(self) -> None:
        cases = (
            (Language.RU, RepeatMode.OFF, "Повтор: выкл."),
            (Language.RU, RepeatMode.QUEUE, "Повтор: очередь"),
            (Language.RU, RepeatMode.TRACK, "Повтор: трек"),
            (Language.EN, RepeatMode.OFF, "Repeat: off"),
            (Language.EN, RepeatMode.QUEUE, "Repeat: queue"),
            (Language.EN, RepeatMode.TRACK, "Repeat: track"),
            (Language.KO, RepeatMode.OFF, "반복: 꺼짐"),
            (Language.KO, RepeatMode.QUEUE, "반복: 대기열"),
            (Language.KO, RepeatMode.TRACK, "반복: 한 곡"),
        )
        for language, mode, expected in cases:
            with self.subTest(language=language, mode=mode):
                adapter = QueuePaginationAdapter(
                    QueueSnapshot(None, (), mode),
                    context=PresentationContext(language, ZoneInfo("UTC")),
                )
                self.assertTrue(
                    (adapter.make_embed(0).footer.text or "").endswith(expected)
                )

    def test_single_track_summary_uses_count_label(self) -> None:
        session = MusicSession(1, tracks=[TrackInfo("Track", "https://example.com")])
        embed = build_session_summary_embed(session, language=Language.EN)

        self.assertTrue((embed.fields[0].value or "").startswith("**Tracks:** 1\n"))

    async def test_public_queue_failure_uses_clickers_private_language(self) -> None:
        interaction = MagicMock(
            guild_id=1,
            locale=discord.Locale.american_english,
            guild_locale=discord.Locale.russian,
        )
        interaction.extras = {}
        cog = MagicMock(spec=MusicCog)
        cog._require_guild = AsyncMock(return_value=MagicMock(id=1))
        cog.service = MagicMock()
        cog.service.get_queue = AsyncMock(
            return_value=MusicResult[None](
                MusicResultStatus.FAILURE,
                "diagnostic only",
                reason=MusicResultReason.QUEUE_EMPTY,
            )
        )
        with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
            command = copy(MusicCog.queue)
            command.binding = cog
            await command._do_call(interaction, {"ephemeral": False})
        self.assertEqual(send.call_args.kwargs["description"], "The queue is empty")
        self.assertTrue(send.call_args.kwargs["ephemeral"])


class TestMusicPresentationAdmission(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.repository = MagicMock(spec=PreferencesRepository)
        self.repository.get = AsyncMock(return_value=Preferences(Language.EN, "UTC"))
        self.bot = MagicMock()
        self.bot.preferences_repository = self.repository
        self.cog = object.__new__(MusicCog)
        BaseCog.__init__(self.cog, self.bot)
        self.service = MagicMock()
        self.cog.service = self.service
        self.session = MusicSession(
            1, tracks=[TrackInfo("Track", "https://example.com")]
        )
        self.payload = TrackExceptionPayload(
            1, make_track("track"), "failure", None, 2, 10
        )

    async def test_undeliverable_notifications_do_not_read_preferences(self) -> None:
        for channel in (None, MagicMock(spec=discord.CategoryChannel)):
            with self.subTest(channel=channel):
                self.bot.get_channel.return_value = channel
                await self.cog.on_music_session_end(1, self.session, 10)
                await self.cog.on_music_track_exception(self.payload)
        self.repository.get.assert_not_awaited()

    async def test_empty_session_and_unaddressed_error_do_not_read_preferences(
        self,
    ) -> None:
        channel = MagicMock(spec=discord.TextChannel, send=AsyncMock())
        self.bot.get_channel.return_value = channel
        await self.cog.on_music_session_end(1, MusicSession(1), 10)
        await self.cog.on_music_track_exception(
            TrackExceptionPayload(1, make_track("track"), "failure", None, 2, None)
        )
        self.repository.get.assert_not_awaited()
        channel.send.assert_not_awaited()

    async def test_delivered_notifications_keep_guild_language(self) -> None:
        channel = MagicMock(spec=discord.TextChannel, send=AsyncMock())
        self.bot.get_channel.return_value = channel
        await self.cog.on_music_session_end(1, self.session, 10)
        summary = channel.send.call_args.kwargs
        self.addCleanup(summary["view"].stop)
        self.assertEqual(summary["embed"].title, "Session ended")
        self.repository.get.assert_awaited_once_with(PreferenceScope.GUILD, 1)
        self.repository.get.reset_mock()

        await self.cog.on_music_track_exception(self.payload)
        self.assertEqual(
            channel.send.call_args.kwargs["embed"].title, "Could not play the track"
        )
        self.repository.get.assert_awaited_once_with(PreferenceScope.GUILD, 1)

    async def test_recovery_check_reads_preferences_only_for_its_private_reply(
        self,
    ) -> None:
        for healing, command, allowed in (
            (False, "play", True),
            (True, "leave", True),
            (True, "queue", True),
            (True, "play", False),
        ):
            with self.subTest(healing=healing, command=command):
                request = interaction()
                request.client.preferences_repository = self.repository
                request.command = MagicMock()
                request.command.name = command
                self.service.voice_lifecycle.is_healing.return_value = healing
                self.repository.get.reset_mock()
                with patch.object(FeedbackUI, "send", new=AsyncMock()) as send:
                    self.assertEqual(await self.cog.interaction_check(request), allowed)
                if allowed:
                    self.repository.get.assert_not_awaited()
                    send.assert_not_awaited()
                else:
                    self.repository.get.assert_awaited_once_with(
                        PreferenceScope.USER, 10
                    )
                    self.assertEqual(
                        send.call_args.kwargs["description"],
                        MusicMessages(catalog("music", Language.EN)).failure(
                            MusicResultReason.RECOVERING
                        ),
                    )
                    self.assertTrue(send.call_args.kwargs["ephemeral"])
