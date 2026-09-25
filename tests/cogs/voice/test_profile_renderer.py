"""Image output contracts without pixel-perfect golden fixtures."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from PIL import GifImagePlugin, Image

from api.progression.appearance import LevelAppearancePolicy
from api.voice.profile.build import build_profile
from api.voice.profile.model import DailyActivityPoint, VoiceProfile, VoiceProfileStats
from api.voice.timeline import VoiceTimeline
from cogs.voice.profile.animation import (
    FRAME_COUNT,
    FRAME_MS,
    render_attachment,
    render_gif,
)
from cogs.voice.profile.renderer import CardIdentity, _display_name, render_png


def fixture(level: int = 1) -> tuple[VoiceProfile, CardIdentity]:
    """Return one data-rich profile and resolved identity for image tests."""
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    profile = build_profile(VoiceTimeline((), (), ()), 1, 2, now, UTC, "UTC")
    days = tuple(
        DailyActivityPoint(
            (now - timedelta(days=13 - index)).date(),
            3600.0 * (index % 5),
            86400,
            86400,
            1,
            True,
            3600.0 * (index % 5),
            index == 13,
        )
        for index in range(14)
    )
    return (
        replace(
            profile,
            level=level,
            appearance=LevelAppearancePolicy().for_level(level),
            total_xp=1_234_567,
            level_earned_xp=900,
            level_required_xp=2000,
            progress_ratio=0.45,
            days=days,
            stats=VoiceProfileStats(83_000, 22, 3600, 0.75, 3, 7000, 18, 4200),
        ),
        CardIdentity(
            "testuser",
            "A long Discord display name that must end in an ellipsis "
            + "when it exceeds the header",
            "Stupid Test Server",
            "Companion",
        ),
    )


class TestProfileRenderer(unittest.TestCase):
    def test_unsupported_name_glyph_uses_username_on_bitmap(self) -> None:
        identity = CardIdentity("username", "User😀", "Guild", "—")
        self.assertEqual(_display_name(identity, 2), "username")

    def test_png_is_decodable_full_size_for_every_tier_landmark(self) -> None:
        for level in (1, 5, 10, 20, 35, 50, 75, 100, 150):
            with self.subTest(level=level):
                data = render_png(*fixture(level))
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertLess(len(data), 1_500_000)
                with Image.open(BytesIO(data)) as image:
                    self.assertEqual(image.size, (1200, 675))
                    image.verify()

    def test_gif_uses_one_loop_with_expected_frames_and_size(self) -> None:
        data = render_gif(*fixture(100))
        self.assertLess(len(data), 5 * 1024 * 1024)
        with Image.open(BytesIO(data)) as image:
            self.assertIsInstance(image, GifImagePlugin.GifImageFile)
            if not isinstance(image, GifImagePlugin.GifImageFile):
                return
            self.assertEqual(image.size, (1200, 675))
            self.assertEqual(image.n_frames, FRAME_COUNT)
            self.assertEqual(image.info["loop"], 0)
            self.assertEqual(image.info["duration"], FRAME_MS)
            image.seek(FRAME_COUNT - 1)
            image.load()

    def test_gif_failure_falls_back_to_png(self) -> None:
        with patch(
            "cogs.voice.profile.animation.render_gif", side_effect=OSError("encode")
        ):
            with self.assertLogs("cogs.voice.profile.animation", level="WARNING"):
                data, extension = render_attachment(*fixture(20))
        self.assertEqual(extension, "png")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_oversized_gif_falls_back_to_png(self) -> None:
        oversized = b"x" * (5 * 1024 * 1024 + 1)
        with patch("cogs.voice.profile.animation.render_gif", return_value=oversized):
            with self.assertLogs("cogs.voice.profile.animation", level="WARNING"):
                data, extension = render_attachment(*fixture(50))
        self.assertEqual(extension, "png")
        self.assertTrue(data.startswith(b"\x89PNG"))
