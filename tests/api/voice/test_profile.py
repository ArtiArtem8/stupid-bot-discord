"""Behavioral voice profile and local-day coverage tests."""

import unittest
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from api.progression.appearance import LevelAppearancePolicy
from api.progression.levels import LevelPolicy
from api.voice.model import VoiceStateSnapshot
from api.voice.profile.build import build_profile
from api.voice.profile.chart import chart_days
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
    def test_empty_history_has_level_one_and_fourteen_unknown_days(self) -> None:
        profile = build_profile(timeline(), 1, 1, NOW, UTC, "UTC")
        self.assertEqual((profile.level, profile.total_xp), (1, 0))
        self.assertEqual(profile.stats.total_voice_seconds, 0)
        self.assertEqual(profile.stats.session_count, 0)
        self.assertEqual(profile.stats.social_ratio, 0)
        self.assertEqual(len(profile.days), 14)
        self.assertTrue(all(not day.usable for day in profile.days))

    def test_companion_maximum_and_id_tie_break(self) -> None:
        start = NOW - timedelta(hours=3)
        profile = build_profile(
            timeline(
                (
                    room(start, start + timedelta(hours=1), (1, 9)),
                    room(
                        start + timedelta(hours=1), start + timedelta(hours=2), (1, 3)
                    ),
                )
            ),
            1,
            1,
            NOW,
            UTC,
            "UTC",
        )
        self.assertEqual(profile.stats.top_companion_id, 3)
        self.assertEqual(profile.stats.top_companion_seconds, 3600)
        self.assertEqual(profile.stats.social_ratio, 1)

    def test_social_ratio_peak_hour_and_guild_scoped_week(self) -> None:
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
            NOW,
            UTC,
            "UTC",
        )
        self.assertEqual(profile.stats.social_ratio, 0.5)
        self.assertEqual(profile.stats.peak_hour, 10)
        self.assertEqual(profile.stats.xp_last_7_days, 300)
        self.assertEqual(profile.total_xp, 1500)
        exact = LevelPolicy().progress(1500)
        self.assertEqual(profile.level, exact.level)
        self.assertEqual(
            profile.appearance, LevelAppearancePolicy().for_level(exact.level)
        )

    def test_fractional_xp_survives_in_render_independent_model(self) -> None:
        start = NOW - timedelta(seconds=1)
        profile = build_profile(
            timeline((room(start, NOW, (1, 2)),)), 1, 1, NOW, UTC, "UTC"
        )
        self.assertEqual(profile.total_xp, 0)
        self.assertEqual(profile.exact_total_xp, Fraction(1, 3))
        self.assertEqual(profile.exact_level_earned_xp, Fraction(1, 3))

    def test_partial_day_and_unknown_gap_break_moving_average(self) -> None:
        start = NOW - timedelta(days=14)
        # Coverage every day except yesterday; today's window is twelve hours.
        coverage = tuple(
            ObservationInterval(
                1, start + timedelta(days=i), start + timedelta(days=i + 1)
            )
            for i in range(14)
            if i != 12
        )
        points = chart_days(timeline(coverage=coverage), 1, 1, NOW, UTC)
        self.assertEqual(len(points), 14)
        self.assertEqual(points[-1].expected_window_seconds, 12 * 3600)
        self.assertFalse(points[-2].usable)
        self.assertIsNone(points[-1].moving_average_seconds)
        self.assertTrue(points[0].usable)
        self.assertEqual(points[0].voice_seconds, 0)

    def test_dst_local_day_uses_real_elapsed_seconds(self) -> None:
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 9, 12, tzinfo=UTC)
        spring_start = datetime(2026, 3, 8, tzinfo=zone).astimezone(UTC)
        spring_end = datetime(2026, 3, 9, tzinfo=zone).astimezone(UTC)
        points = chart_days(
            timeline(coverage=(ObservationInterval(1, spring_start, spring_end),)),
            1,
            1,
            now,
            zone,
        )
        spring = next(
            point for point in points if point.day.isoformat() == "2026-03-08"
        )
        self.assertEqual(spring.expected_window_seconds, 23 * 3600)
        self.assertEqual(spring.coverage_ratio, 1)

    def test_coverage_below_ninety_percent_is_not_a_zero(self) -> None:
        day = NOW.replace(hour=0)
        short = ObservationInterval(1, day, day + timedelta(hours=10))
        points = chart_days(timeline(coverage=(short,)), 1, 1, NOW, UTC)
        self.assertFalse(points[-1].usable)
        self.assertLess(points[-1].coverage_ratio, 0.9)
        self.assertEqual(points[-1].voice_seconds, 0)
