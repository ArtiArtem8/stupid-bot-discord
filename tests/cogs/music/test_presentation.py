"""Tests for pure music presentation helpers."""

import unittest

from api.music import MusicSession
from cogs.music.presentation import build_session_summary_embed, format_track_link


class TestTrackLinkFormatting(unittest.TestCase):
    def test_formats_title_with_uri(self) -> None:
        self.assertEqual(
            format_track_link("A track", "https://example.com/track"),
            "[A track](https://example.com/track)",
        )

    def test_escapes_markdown_in_title(self) -> None:
        self.assertEqual(
            format_track_link("**Bold**", "https://example.com/track"),
            r"[\*\*Bold\*\*](https://example.com/track)",
        )

    def test_omits_link_when_uri_is_missing(self) -> None:
        self.assertEqual(format_track_link("A track", None), "A track")


class TestSessionSummaryPresentation(unittest.TestCase):
    def test_one_real_requester_is_displayed(self) -> None:
        session = MusicSession(guild_id=1)
        session.add_track(
            title="Track",
            uri="https://example.com/track",
            requester_id=42,
            channel_id=100,
        )

        embed = build_session_summary_embed(session)

        stats = embed.fields[0].value
        if not isinstance(stats, str):
            self.fail("Session summary stats field is missing")
        self.assertIn("**Заказчик:** <@42>", stats)

    def test_multiple_requesters_are_counted(self) -> None:
        session = MusicSession(guild_id=1)
        for requester_id in (42, 84):
            session.add_track(
                title=f"Track {requester_id}",
                uri=f"https://example.com/{requester_id}",
                requester_id=requester_id,
                channel_id=100,
            )

        embed = build_session_summary_embed(session)

        stats = embed.fields[0].value
        if not isinstance(stats, str):
            self.fail("Session summary stats field is missing")
        self.assertIn("**Заказчиков:** 2 чел.", stats)
