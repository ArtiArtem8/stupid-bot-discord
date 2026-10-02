"""Pooled voice pace, evidence guards and observation semantics."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.prediction import VoiceHoursEstimate, estimate_voice_hours
from api.voice.scope import VoiceScope, local_calendar_range
from api.voice.timeline import ObservationInterval, RoomInterval, VoiceTimeline
from tests.api.voice.examples import at, human


class TestVoiceHoursPrediction(unittest.TestCase):
    def test_duration_weighted_pace_uses_supplied_policy_and_excludes_future(
        self,
    ) -> None:
        rooms = (
            RoomInterval(1, 10, at(0), at(3600), (human(),)),
            RoomInterval(1, 10, at(3600), at(14400), (human(), human(2))),
            RoomInterval(1, 10, at(14400), at(86400), (human(),)),
        )
        timeline = VoiceTimeline(rooms, (), ())
        scope = VoiceScope(1, time_range=local_calendar_range(at(14400), UTC, 30))
        policy = VoiceXpPolicy(
            solo_per_hour=Fraction(101, 3), social_per_hour=Fraction(1321, 7)
        )
        xp = policy.calculate(timeline, 1, scope)
        estimate = estimate_voice_hours(
            timeline,
            1,
            scope,
            UTC,
            remaining_xp=Fraction(1234, 7),
            recent_xp=xp,
            xp_policy=policy,
        )
        if estimate is None:
            self.fail("Four observed earning hours must produce a point estimate")
        self.assertEqual(estimate.pace_xp_per_hour, xp / 4)
        self.assertEqual(
            estimate.expected_hours * estimate.pace_xp_per_hour, Fraction(1234, 7)
        )
        self.assertEqual(estimate.evidence_hours, 4)
        self.assertEqual(estimate.active_days, 1)

    def test_fragmentation_and_guild_coverage_do_not_reweight_confirmed_voice(
        self,
    ) -> None:
        room = RoomInterval(1, 10, at(0), at(7200), (human(), human(2)))
        whole = VoiceTimeline(
            (room,), (), (ObservationInterval(1, at(-86400), at(7200)),)
        )
        fragments = VoiceTimeline(
            tuple(
                replace(room, started_at=at(i), ended_at=at(i + 600))
                for i in range(0, 7200, 600)
            ),
            (),
            (ObservationInterval(1, at(0), at(7200)),),
        )
        scope = VoiceScope(1, time_range=local_calendar_range(at(7200), UTC, 30))
        policy = VoiceXpPolicy()

        def predict(timeline: VoiceTimeline) -> VoiceHoursEstimate | None:
            return estimate_voice_hours(
                timeline,
                1,
                scope,
                UTC,
                remaining_xp=Fraction(5000),
                recent_xp=policy.calculate(timeline, 1, scope),
                xp_policy=policy,
            )

        self.assertIsNotNone(predict(whole))
        self.assertEqual(predict(whole), predict(fragments))

    def test_short_or_non_earning_evidence_has_no_prediction(self) -> None:
        scope = VoiceScope(1, time_range=local_calendar_range(at(86400), UTC, 30))
        policy = VoiceXpPolicy()
        for states, seconds in (
            ((human(),), 660),
            ((replace(human(), afk=True),), 7200),
        ):
            with self.subTest(seconds=seconds):
                timeline = VoiceTimeline(
                    (RoomInterval(1, 10, at(0), at(seconds), states),), (), ()
                )
                self.assertIsNone(
                    estimate_voice_hours(
                        timeline,
                        1,
                        scope,
                        UTC,
                        remaining_xp=Fraction(1000),
                        recent_xp=policy.calculate(timeline, 1, scope),
                        xp_policy=policy,
                    )
                )

    def test_half_open_active_day_count_across_dst_uses_elapsed_voice_hours(
        self,
    ) -> None:
        zone = ZoneInfo("America/New_York")
        policy = VoiceXpPolicy()
        for month, day, expected_hours in ((3, 8, 23), (11, 1, 25)):
            with self.subTest(month=month):
                start = datetime(2026, month, day, tzinfo=zone).astimezone(UTC)
                end = datetime(2026, month, day + 1, tzinfo=zone).astimezone(UTC)
                timeline = VoiceTimeline(
                    (RoomInterval(1, 10, start, end, (human(),)),), (), ()
                )
                scope = VoiceScope(
                    1,
                    time_range=local_calendar_range(end + timedelta(hours=1), zone, 30),
                )
                result = estimate_voice_hours(
                    timeline,
                    1,
                    scope,
                    zone,
                    remaining_xp=Fraction(5000),
                    recent_xp=policy.calculate(timeline, 1, scope),
                    xp_policy=policy,
                )
                if result is None:
                    self.fail("The observed DST date contains earning voice")
                self.assertEqual(result.active_days, 1)
                self.assertEqual(result.evidence_hours, expected_hours)

    def test_unbounded_and_global_scopes_are_rejected(self) -> None:
        for scope in (
            VoiceScope(1),
            VoiceScope(time_range=local_calendar_range(at(0), UTC, 30)),
        ):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                estimate_voice_hours(
                    VoiceTimeline((), (), ()),
                    1,
                    scope,
                    UTC,
                    remaining_xp=Fraction(1000),
                    recent_xp=Fraction(100),
                    xp_policy=VoiceXpPolicy(),
                )
