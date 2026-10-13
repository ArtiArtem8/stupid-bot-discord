"""Tests for pure music presentation helpers."""

import unittest

from stupidbot.discord_ui.theme import Color
from stupidbot.features.music.discord.presentation import (
    build_playlist_added_embed,
    build_repeat_embed,
    build_session_summary_embed,
    format_track_link,
)
from stupidbot.features.music.playback.models import PlayPlacement, RepeatMode
from stupidbot.features.music.results import PlaylistResponseData
from stupidbot.features.music.runtime.session import MusicSession
from tests.features.music.fixtures import make_playlist, make_track


class TestTrackLinkFormatting(unittest.TestCase):
    def test_long_label_keeps_complete_link(self) -> None:
        uri = "https://example.com/track"
        value = format_track_link("[long]" * 1000, uri)
        self.assertLessEqual(len(value), 1024)
        self.assertTrue(value.endswith(f"]({uri})"))

    def test_oversized_uri_falls_back_to_bounded_label(self) -> None:
        value = format_track_link("Track", "https://example.com/" + "x" * 2000)
        self.assertEqual(value, "Track")

    def test_repeat_modes_use_informational_color(self) -> None:
        for mode in RepeatMode:
            with self.subTest(mode=mode):
                colour = build_repeat_embed(mode).colour
                self.assertIsNotNone(colour)
                self.assertEqual(colour.value if colour else None, Color.INFO)

    def test_formats_safe_and_hostile_titles_with_uri(self) -> None:
        uri = "https://good.invalid"
        cases = (
            ("A track", "[A track](https://good.invalid)"),
            ("**Bold**", r"[\*\*Bold\*\*](https://good.invalid)"),
            ("Song [Live]", "[Song [Live]](https://good.invalid)"),
            (
                "Artist [Album [Live]]",
                "[Artist [Album [Live]]](https://good.invalid)",
            ),
            ("Song [Live...", "Song [Live..."),
            (
                "Song ]Live[",
                "Song ]Live[",
            ),
            (
                "Song ](https://evil.invalid)",
                "Song ](https://evil.invalid)",
            ),
            (
                "[fake](https://evil.invalid)",
                r"\[fake](https://evil.invalid)",
            ),
            (
                r"Song \ Remix",
                r"[Song \\ Remix](https://good.invalid)",
            ),
            (
                "Line one\nLine two",
                "[Line one Line two](https://good.invalid)",
            ),
        )

        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(format_track_link(title, uri), expected)

    def test_plain_titles_without_uri_escape_only_markdown_syntax(self) -> None:
        cases = (
            ("Song [Live]", "Song [Live]"),
            (
                "Song ](https://evil.invalid)",
                "Song ](https://evil.invalid)",
            ),
            (
                "[fake](https://evil.invalid)",
                r"\[fake](https://evil.invalid)",
            ),
        )

        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(format_track_link(title, None), expected)

    def test_collapses_multiline_title_without_uri(self) -> None:
        self.assertEqual(
            format_track_link("Line one\nLine two", None), "Line one Line two"
        )

    def test_truncating_balanced_title_closes_bracket_inside_link(self) -> None:
        uri = "https://example.com/track"
        value = format_track_link("Song [a very long live title]", uri, max_length=50)

        self.assertEqual(value, f"[Song [a very long...]]({uri})")
        self.assertLessEqual(len(value), 50)

    def test_title_budget_reserves_nested_closing_brackets(self) -> None:
        uri = "https://example.com/track"
        for width, label in ((11, "A [B [...]]"), (4, "A..."), (3, "...")):
            with self.subTest(width=width):
                self.assertEqual(
                    format_track_link("A [B [C] D] E", uri, max_title_length=width),
                    f"[{label}]({uri})",
                )


class TestPlaylistPresentation(unittest.TestCase):
    def test_long_playlist_name_fits_title_with_closed_emphasis(self) -> None:
        data: PlaylistResponseData = {
            "type": "playlist",
            "playlist": make_playlist("x" * 500, []),
            "undo_entries": (),
            "placement": "end",
        }
        embed = build_playlist_added_embed(
            data, requester_name="User", requester_avatar_url=""
        )
        self.assertLessEqual(len(embed.title or ""), 256)
        self.assertTrue((embed.title or "").endswith("**"))

    def test_escapes_and_normalizes_playlist_name(self) -> None:
        playlist = make_playlist(
            "**Mix** [text](https://evil.invalid)\nSecond line",
            [make_track("one")],
        )
        cases: dict[PlayPlacement, tuple[str, str]] = {
            "next": (
                "Плейлист добавлен в начало очереди",
                (
                    r"**\*\*Mix\*\* \[text](https://evil.invalid) "
                    r"Second line**"
                    "\nТреков: 1"
                ),
            ),
            "end": (
                (
                    r"Добавлен плейлист **\*\*Mix\*\* "
                    r"\[text](https://evil.invalid) Second line**"
                ),
                "Треков: 1",
            ),
        }

        for placement, (expected_title, expected_description) in cases.items():
            with self.subTest(placement=placement):
                data: PlaylistResponseData = {
                    "type": "playlist",
                    "playlist": playlist,
                    "undo_entries": (),
                    "placement": placement,
                }
                embed = build_playlist_added_embed(
                    data,
                    requester_name="Requester",
                    requester_avatar_url="https://example.com/avatar.png",
                )

                self.assertEqual(embed.title, expected_title)
                self.assertEqual(embed.description, expected_description)


class TestSessionSummaryPresentation(unittest.TestCase):
    def test_truncated_bracketed_title_keeps_link(self) -> None:
        session = MusicSession(guild_id=1)
        session.add_track(
            "Song [Live recording with a deliberately long title]",
            "https://example.com/track",
            42,
            100,
            skipped=True,
        )

        value = build_session_summary_embed(session).fields[1].value or ""

        self.assertTrue(value.startswith("~~[Song [Live"))
        self.assertTrue(value.endswith("...]](https://example.com/track)~~"))
        self.assertNotIn("\\", value)

    def test_bracketed_skipped_tracks_keep_link_and_repeat_count(self) -> None:
        session = MusicSession(guild_id=1)
        for _ in range(2):
            session.add_track(
                title="Song [Live]",
                uri="https://example.com/track",
                requester_id=42,
                channel_id=100,
                skipped=True,
            )

        embed = build_session_summary_embed(session)

        self.assertEqual(
            embed.fields[1].value,
            "~~[Song [Live]](https://example.com/track) **×2**~~",
        )

    def test_long_recent_track_preserves_link_and_surrounding_markup(self) -> None:
        uri = "https://example.com/" + "x" * 981
        session = MusicSession(guild_id=1)
        session.add_track("Earlier", "https://example.com/old", 42, 100)
        for _ in range(2):
            session.add_track("Song [Live]", uri, 42, 100, skipped=True)

        value = build_session_summary_embed(session).fields[1].value or ""

        self.assertLessEqual(len(value), 1024)
        self.assertTrue(value.startswith("~~["))
        self.assertTrue(value.endswith(f"]({uri}) **×2**~~\n..."))

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
