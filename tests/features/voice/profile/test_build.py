"""Compact voice profile lifetime projection."""

import unittest
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from stupidbot.features.voice.facts import GapReason, ObservationGap, VoiceStateSnapshot
from stupidbot.features.voice.levels import LevelPolicy
from stupidbot.features.voice.profile.appearance import LevelAppearancePolicy
from stupidbot.features.voice.profile.build import build_profile
from stupidbot.features.voice.profile.details import (
    build_people_detail,
    build_xp_detail,
)
from stupidbot.features.voice.timeline import (
    ObservationInterval,
    RoomInterval,
    VoiceTimeline,
)

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
        self.assertIsNone(profile.history_since)

    def test_history_start_is_shared_guild_evidence_in_the_profile_timezone(
        self,
    ) -> None:
        start = datetime(2026, 9, 20, 23, tzinfo=UTC)
        end = start + timedelta(minutes=30)
        unrelated = room(start - timedelta(days=10), end, (1,), guild=2)
        cases = {
            "another member's room": VoiceTimeline(
                (unrelated, room(start, end, (2,))), (), ()
            ),
            "empty room coverage": VoiceTimeline(
                (unrelated,), (), (ObservationInterval(1, start, end),)
            ),
            "guild gap": VoiceTimeline(
                (unrelated,),
                (ObservationGap(start, end, GapReason.UNKNOWN, guild_id=1),),
                (),
            ),
            "global gap": VoiceTimeline(
                (unrelated,), (ObservationGap(start, end, GapReason.UNKNOWN),), ()
            ),
        }
        zone = ZoneInfo("Asia/Krasnoyarsk")
        for source, history in cases.items():
            with self.subTest(source=source):
                profile = build_profile(history, 1, 1, str(zone))
                self.assertEqual(profile.history_since, date(2026, 9, 21))
                self.assertEqual(profile.total_voice_seconds, 0)
                for detail in (
                    build_people_detail(history, 1, 1, NOW, zone),
                    build_xp_detail(history, 1, 1, NOW, zone),
                ):
                    if detail.lifetime_period is None:
                        self.fail("Retained guild evidence must establish history")
                    self.assertEqual(
                        detail.lifetime_period.first_date, profile.history_since
                    )

    def test_another_guild_does_not_establish_profile_history(self) -> None:
        start = NOW - timedelta(days=1)
        history = VoiceTimeline(
            (room(start, NOW, (1,), guild=2),),
            (ObservationGap(start, NOW, GapReason.UNKNOWN, guild_id=2),),
            (ObservationInterval(2, start, NOW),),
        )
        self.assertIsNone(build_profile(history, 1, 1, "UTC").history_since)

    def test_details_have_no_lifetime_window_before_history_begins(self) -> None:
        history = timeline(
            coverage=(ObservationInterval(1, NOW, NOW + timedelta(hours=1)),)
        )
        for as_of in (NOW - timedelta(seconds=1), NOW):
            with self.subTest(as_of=as_of):
                self.assertIsNone(
                    build_people_detail(history, 1, 1, as_of).lifetime_period
                )
                self.assertIsNone(build_xp_detail(history, 1, 1, as_of).lifetime_period)

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
        levels = LevelPolicy()
        durations = [1]
        for level in (2, 3, 5):
            boundary_seconds = 3 * levels.threshold(level)
            durations.extend(boundary_seconds + offset for offset in (-1, 0, 1))
        for seconds in durations:
            with self.subTest(seconds=seconds):
                start = NOW - timedelta(seconds=seconds)
                profile = build_profile(
                    timeline((room(start, NOW, (1, 2)),)), 1, 1, "UTC"
                )
                exact = levels.progress(Fraction(seconds, 3))
                self.assertEqual(profile.total_xp, int(exact.total_xp))
                self.assertEqual(profile.level, exact.level)
                self.assertEqual(profile.level_earned_xp, int(exact.earned))
                self.assertEqual(profile.level_required_xp, exact.required)
                self.assertEqual(profile.progress_ratio, float(exact.ratio))
                self.assertEqual(
                    profile.appearance,
                    LevelAppearancePolicy().for_level(exact.level),
                )
