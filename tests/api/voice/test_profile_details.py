"""Calendar, coverage and focused detail projection contracts."""

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api.progression.levels import LevelPolicy
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.model import VoiceStateSnapshot
from api.voice.profile import details
from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.scope import VoiceScope
from api.voice.timeline import ObservationInterval, RoomInterval, VoiceTimeline
from tests.api.voice.examples import at, example, human


class TestActivityDetail(unittest.TestCase):
    def test_calendar_window_dst_and_current_partial_day(self) -> None:
        zone = ZoneInfo("America/New_York")
        for month, day, hours in ((3, 8, 23), (11, 1, 25)):
            with self.subTest(month=month):
                as_of = datetime(2026, month, day + 1, 12, tzinfo=zone)
                empty = VoiceTimeline((), (), ())
                result = build_activity_detail(empty, 1, 1, as_of, zone)
                self.assertEqual(len(result.days), 30)
                self.assertEqual(result.days[-2].possible_seconds, hours * 3600)
                self.assertEqual(result.days[-1].possible_seconds, 12 * 3600)
                self.assertEqual(result.days[-1].date, as_of.date())
                self.assertEqual(result.days[0].date, as_of.date() - timedelta(days=29))
                self.assertIsNone(result.peak_hour)
                self.assertEqual(result.peak_weekdays, ())

    def test_known_empty_partial_and_unknown_days_are_distinct(self) -> None:
        start = datetime(2026, 10, 1, tzinfo=UTC)
        timeline = VoiceTimeline(
            (),
            (),
            (
                ObservationInterval(1, start, start + timedelta(days=1)),
                ObservationInterval(
                    1, start + timedelta(days=1), start + timedelta(hours=36)
                ),
                ObservationInterval(2, start, start + timedelta(days=3)),
            ),
        )
        result = build_activity_detail(
            timeline, 1, 1, start + timedelta(days=2, hours=12)
        )
        days = result.days[-3:]
        self.assertEqual([day.voice_seconds for day in days], [0, 0, 0])
        self.assertEqual([day.coverage_ratio for day in days], [1, 0.5, 0])
        self.assertEqual(result.period.observed_seconds, 36 * 3600)

    def test_midnight_has_thirty_columns_and_zero_length_today(self) -> None:
        result = build_activity_detail(VoiceTimeline((), (), ()), 1, 1, at(0))
        self.assertEqual(result.days[-1].possible_seconds, 0)
        self.assertEqual(result.days[-1].coverage_ratio, 0)
        self.assertEqual(len(result.days), 30)

    def test_presence_sessions_and_peaks_share_the_window(self) -> None:
        result = build_activity_detail(example(), 1, 1, at(1200))
        self.assertEqual(result.presence.total_seconds, 1200)
        self.assertEqual(result.presence.solo_seconds, 300)
        self.assertEqual(result.presence.group_seconds, 900)
        self.assertEqual(result.presence.session_count, 1)
        self.assertEqual(result.presence.average_session_seconds, 1200)
        self.assertEqual(result.presence.median_session_seconds, 1200)
        self.assertEqual((result.peak_hour, result.peak_weekdays), (0, (0,)))
        self.assertEqual(result.days[-1].voice_seconds, 1200)
        self.assertEqual(result.days[-1].date, date(2026, 9, 21))

    def test_old_and_other_guild_rooms_do_not_change_peaks(self) -> None:
        old = RoomInterval(1, 10, at(-40 * 86400), at(-39 * 86400), (human(),))
        other = RoomInterval(2, 10, at(0), at(86400), (human(),))
        result = build_activity_detail(
            VoiceTimeline((old, other), (), ()), 1, 1, at(86400)
        )
        self.assertEqual(result.presence.total_seconds, 0)
        self.assertIsNone(result.peak_hour)

    def test_close_peak_days_use_total_voice_time_and_monday_first_order(self) -> None:
        rooms = (
            RoomInterval(1, 10, at(0), at(96), (human(),)),
            RoomInterval(1, 10, at(3 * 86400), at(3 * 86400 + 100), (human(),)),
            RoomInterval(1, 10, at(5 * 86400), at(5 * 86400 + 94), (human(),)),
        )
        result = build_activity_detail(
            VoiceTimeline(rooms, (), ()), 1, 1, at(7 * 86400)
        )
        self.assertEqual(result.peak_weekdays, (0, 3))

    def test_close_peak_boundary_and_balanced_week(self) -> None:
        for other, expected in ((94.99, (0,)), (95, (0, 1)), (100, (0, 1))):
            with self.subTest(other=other):
                rooms = (
                    RoomInterval(1, 10, at(0), at(100), (human(),)),
                    RoomInterval(1, 10, at(86400), at(86400 + other), (human(),)),
                )
                result = build_activity_detail(
                    VoiceTimeline(rooms, (), ()), 1, 1, at(2 * 86400)
                )
                self.assertEqual(result.peak_weekdays, expected)
        rooms = tuple(
            RoomInterval(1, 10, at(day * 86400), at(day * 86400 + 100), (human(),))
            for day in range(7)
        )
        result = build_activity_detail(
            VoiceTimeline(rooms, (), ()), 1, 1, at(7 * 86400)
        )
        self.assertEqual(result.peak_weekdays, tuple(range(7)))


class TestPeopleAndXpDetails(unittest.TestCase):
    def test_next_level_estimate_uses_voice_hours_and_exact_remaining_xp(self) -> None:
        timeline = VoiceTimeline(
            (RoomInterval(1, 10, at(0), at(3600), (human(), human(2))),), (), ()
        )
        result = build_xp_detail(timeline, 1, 1, at(3600))
        estimate = result.estimate
        if estimate is None:
            self.fail("Observed earning voice time must produce an estimate")
        self.assertIsInstance(estimate.expected_hours, Fraction)
        remaining = LevelPolicy().progress(result.breakdown.total).remaining
        self.assertEqual(estimate.expected_hours * estimate.pace_xp_per_hour, remaining)

    def test_changed_weekly_pace_uses_pooled_voice_hours_without_extrapolating_trend(
        self,
    ) -> None:
        timeline = VoiceTimeline(
            (
                RoomInterval(
                    1, 10, at(-10 * 86400), at(-10 * 86400 + 7200), (human(),)
                ),
                RoomInterval(1, 10, at(0), at(3600), (human(), human(2))),
            ),
            (),
            (),
        )
        result = build_xp_detail(timeline, 1, 1, at(3600))
        estimate = result.estimate
        if estimate is None:
            self.fail("Both recent rates are observed")
        remaining = LevelPolicy().progress(result.breakdown.total).remaining
        self.assertEqual(estimate.expected_hours * result.recent_xp / 3, remaining)

    def test_no_recent_voice_has_no_estimate(self) -> None:
        result = build_xp_detail(VoiceTimeline((), (), ()), 1, 1, at(0))
        self.assertIsNone(result.estimate)

    def test_inactive_week_does_not_forecast_from_an_old_monthly_rate(self) -> None:
        timeline = VoiceTimeline(
            (RoomInterval(1, 10, at(-10 * 86400), at(-10 * 86400 + 3600), (human(),)),),
            (),
            (),
        )
        estimate = build_xp_detail(timeline, 1, 1, at(0)).estimate
        self.assertIsNone(estimate)

    def test_zero_xp_in_active_week_does_not_forecast_from_old_earning_pace(
        self,
    ) -> None:
        timeline = VoiceTimeline(
            (
                RoomInterval(
                    1, 10, at(-10 * 86400), at(-10 * 86400 + 3600), (human(),)
                ),
                RoomInterval(1, 10, at(0), at(3600), (replace(human(), afk=True),)),
            ),
            (),
            (),
        )
        result = build_xp_detail(timeline, 1, 1, at(3600))
        self.assertGreater(result.recent_xp, 0)
        self.assertIsNone(result.estimate)

    def test_lifetime_people_and_xp_keep_recent_summary_separate(self) -> None:
        old_start, old_end = at(-40 * 86400), at(-40 * 86400 + 3600)
        timeline = VoiceTimeline(
            (
                RoomInterval(1, 10, old_start, old_end, (human(), human(2))),
                RoomInterval(1, 10, at(0), at(600), (human(), human(3))),
            ),
            (),
            (
                ObservationInterval(1, old_start, old_end),
                ObservationInterval(1, at(0), at(600)),
            ),
        )
        people = build_people_detail(timeline, 1, 1, at(600))
        self.assertEqual([person.user_id for person in people.companions], [2, 3])
        self.assertEqual(people.unique_people, 2)
        self.assertEqual(people.recent_unique_people, 1)
        self.assertEqual(people.presence.group_seconds, 4200)
        self.assertEqual(people.recent_presence.group_seconds, 600)
        self.assertIsNotNone(people.lifetime_period)
        xp = build_xp_detail(timeline, 1, 1, at(600))
        self.assertEqual(
            xp.breakdown, VoiceXpPolicy().explain(timeline, 1, VoiceScope(1))
        )
        self.assertEqual(
            xp.recent_xp,
            VoiceXpPolicy()
            .explain(timeline, 1, VoiceScope(1, time_range=xp.period.time_range))
            .total,
        )
        self.assertGreater(xp.breakdown.total, xp.recent_xp)
        self.assertEqual(xp.lifetime_period, people.lifetime_period)

    def test_people_sort_shared_then_private_then_id_and_count_confirmed_humans(
        self,
    ) -> None:
        rooms = (
            RoomInterval(1, 10, at(0), at(60), (human(), human(3), human(2))),
            RoomInterval(1, 10, at(60), at(120), (human(), human(4))),
        )
        result = build_people_detail(VoiceTimeline(rooms, (), ()), 1, 1, at(120))
        self.assertEqual([person.user_id for person in result.companions], [4, 2, 3])
        self.assertEqual(result.unique_people, 3)
        self.assertEqual(result.private_seconds, 60)

    def test_unknown_blocks_private_and_bots_do_not_double_time(self) -> None:
        states = (
            human(),
            human(2),
            VoiceStateSnapshot(8, 10, True),
            VoiceStateSnapshot(9, 10, True),
        )
        rooms = (
            RoomInterval(1, 10, at(0), at(60), states),
            RoomInterval(1, 10, at(60), at(120), (*states, VoiceStateSnapshot(7, 10))),
        )
        result = build_people_detail(VoiceTimeline(rooms, (), ()), 1, 1, at(120))
        self.assertEqual(result.unique_people, 1)
        self.assertEqual(result.companions[0].shared_seconds, 120)
        self.assertEqual(result.private_seconds, 60)
        self.assertEqual(result.bots.any_bot_seconds, 120)
        self.assertEqual(result.bots.by_bot, ((8, 120), (9, 120)))

    def test_xp_is_exact_policy_explanation_without_other_analyses(self) -> None:
        timeline = example()
        with (
            patch.object(details, "companions", side_effect=AssertionError("unneeded")),
            patch.object(details, "activity", side_effect=AssertionError("unneeded")),
            patch.object(
                details, "bot_presence", side_effect=AssertionError("unneeded")
            ),
        ):
            result = build_xp_detail(timeline, 1, 1, at(1101))
        expected = VoiceXpPolicy().explain(
            timeline, 1, VoiceScope(1, time_range=result.period.time_range)
        )
        self.assertEqual(result.breakdown, expected)
        self.assertIsInstance(result.breakdown.total, Fraction)
        self.assertIsInstance(result.breakdown.solo_base, Fraction)
        self.assertGreaterEqual(result.breakdown.audio_reduction, 0)
        self.assertGreaterEqual(result.breakdown.bonus_cap_reduction, 0)

    def test_all_details_use_identical_scope_and_coverage(self) -> None:
        results = [
            builder(example(), 1, 1, at(1200), ZoneInfo("Europe/Moscow"))
            for builder in (build_activity_detail, build_people_detail, build_xp_detail)
        ]
        self.assertEqual(results[0].period, results[1].period)
        self.assertEqual(results[0].period, results[2].period)

    def test_overlapping_coverage_is_counted_once(self) -> None:
        timeline = VoiceTimeline(
            (),
            (),
            (
                ObservationInterval(1, at(0), at(600)),
                ObservationInterval(1, at(300), at(900)),
            ),
        )
        result = build_xp_detail(timeline, 1, 1, at(700))
        self.assertEqual(result.period.observed_seconds, 700)
