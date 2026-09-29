"""Compact voice profile lifetime projection."""

import unittest
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from api.progression.appearance import LevelAppearancePolicy
from api.progression.levels import LevelPolicy
from api.voice.model import VoiceStateSnapshot
from api.voice.profile.build import build_profile
from api.voice.timeline import ObservationInterval, RoomInterval, VoiceTimeline

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def room(
    start: datetime, end: datetime, users: tuple[int, ...], guild: int = 1
) -> RoomInterval:
    return RoomInterval(
        guild,
        10,
        start,
        end,
        tuple(VoiceStateSnapshot(user, 10, is_bot=False) for user in users),
    )


def timeline(
    rooms: tuple[RoomInterval, ...] = (),
    coverage: tuple[ObservationInterval, ...] = (),
) -> VoiceTimeline:
    return VoiceTimeline(rooms, (), coverage)


class TestProfile(unittest.TestCase):
    def test_empty_history_has_level_one_and_zero_presence(self) -> None:
        profile = build_profile(timeline(), 1, 1, "UTC")
        self.assertEqual((profile.level, profile.total_xp), (1, 0))
        self.assertEqual(profile.total_voice_seconds, 0)
        self.assertEqual(profile.session_count, 0)
        self.assertEqual(profile.progress_ratio, 0)
        self.assertEqual(profile.timezone_label, "UTC")

    def test_lifetime_presence_and_xp_are_guild_scoped(self) -> None:
        old = NOW - timedelta(days=9)
        recent = NOW - timedelta(hours=2)
        profile = build_profile(
            timeline(
                (
                    room(old, old + timedelta(hours=1), (1, 2)),
                    room(recent, recent + timedelta(hours=1), (1,)),
                    room(recent, recent + timedelta(hours=1), (1, 2), guild=2),
                )
            ),
            1,
            1,
            "Asia/Krasnoyarsk",
        )
        self.assertEqual(profile.total_voice_seconds, 7200)
        self.assertEqual(profile.session_count, 2)
        self.assertEqual(profile.total_xp, 1500)
        exact = LevelPolicy().progress(1500)
        self.assertEqual(profile.level, exact.level)
        self.assertEqual(
            profile.appearance, LevelAppearancePolicy().for_level(exact.level)
        )
        self.assertEqual(profile.timezone_label, "Asia/Krasnoyarsk")

    def test_fractional_xp_is_preserved_until_display_rounding(self) -> None:
        for seconds in (1, 749, 750, 751):
            with self.subTest(seconds=seconds):
                start = NOW - timedelta(seconds=seconds)
                profile = build_profile(
                    timeline((room(start, NOW, (1, 2)),)), 1, 1, "UTC"
                )
                exact = LevelPolicy().progress(Fraction(seconds, 3))
                self.assertEqual(profile.total_xp, int(exact.total_xp))
                self.assertEqual(profile.level, exact.level)
                self.assertEqual(profile.level_earned_xp, int(exact.earned))
                self.assertEqual(profile.level_required_xp, exact.required)
                self.assertEqual(profile.progress_ratio, float(exact.ratio))
