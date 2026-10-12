"""Music presentation keeps public snapshots separate from private feedback."""

import unittest
from copy import copy
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.discord.presentation import build_track_added_embed
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
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.music.fixtures import make_track


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
