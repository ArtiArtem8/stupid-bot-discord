"""Clock locations preserve observed time independently of grouped sessions."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from api.voice.model import VoiceCheckpoint, VoiceObservation, VoiceSnapshot
from api.voice.profile.calendar import ClockSpan
from api.voice.profile.details import build_activity_detail
from api.voice.timeline import (
    ObservationInterval,
    RoomInterval,
    VoiceTimeline,
    build_timeline,
)
from tests.api.voice.examples import at, human, record


class TestProfileCalendar(unittest.TestCase):
    def test_midnight_splits_clock_columns_but_not_session(self) -> None:
        timeline = build_timeline(
            [
                record(86340, VoiceSnapshot((human(),))),
                record(86400, VoiceCheckpoint()),
                record(86460, VoiceCheckpoint()),
            ]
        )
        result = build_activity_detail(timeline, 1, 1, at(86460))
        previous, current = result.days[-2:]
        self.assertEqual(previous.voice_spans, (ClockSpan(1439, 1440),))
        self.assertEqual(current.voice_spans, (ClockSpan(0, 1),))
        self.assertEqual(current.future_spans, (ClockSpan(1, 1440),))
        self.assertEqual(current.missing_spans, ())
        self.assertEqual(result.presence.session_count, 1)
        self.assertEqual(sum(day.voice_seconds for day in result.days), 120)

    def test_grouped_session_keeps_its_absence_unpainted(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(60, VoiceObservation(replace(human(), self_mute=True))),
                record(120, VoiceObservation(human(1, None))),
                record(180, VoiceObservation(human())),
                record(240, VoiceCheckpoint()),
            ]
        )
        result = build_activity_detail(timeline, 1, 1, at(240))
        day = result.days[-1]
        self.assertEqual(day.voice_spans, (ClockSpan(0, 2), ClockSpan(3, 4)))
        self.assertEqual(day.missing_spans, ())
        self.assertEqual(day.voice_seconds, 180)
        self.assertEqual(result.presence.session_count, 1)
        self.assertEqual(result.presence.average_session_seconds, 180)

    def test_unknown_time_known_absence_and_future_are_distinct(self) -> None:
        timeline = VoiceTimeline(
            (RoomInterval(1, 10, at(7200), at(10800), (human(),)),),
            (),
            (
                ObservationInterval(1, at(0), at(14400)),
                ObservationInterval(1, at(28800), at(43200)),
            ),
        )
        day = build_activity_detail(timeline, 1, 1, at(43200)).days[-1]
        self.assertEqual(day.voice_spans, (ClockSpan(120, 180),))
        self.assertEqual(day.missing_spans, (ClockSpan(240, 480),))
        self.assertEqual(day.future_spans, (ClockSpan(720, 1440),))
        self.assertEqual(day.observed_seconds, 8 * 3600)
        self.assertEqual(day.possible_seconds, 12 * 3600)

    def test_overlapping_coverage_does_not_hide_gaps_or_double_seconds(self) -> None:
        timeline = VoiceTimeline(
            (),
            (),
            (
                ObservationInterval(1, at(0), at(120)),
                ObservationInterval(1, at(60), at(180)),
                ObservationInterval(1, at(240), at(300)),
                ObservationInterval(2, at(0), at(600)),
            ),
        )
        day = build_activity_detail(timeline, 1, 1, at(600)).days[-1]
        self.assertEqual(day.observed_seconds, 240)
        self.assertEqual(day.missing_spans, (ClockSpan(3, 4), ClockSpan(5, 10)))

    def test_full_dst_days_keep_elapsed_duration_and_mark_clock_changes(self) -> None:
        cases = (
            (
                "America/New_York",
                3,
                8,
                23,
                (ClockSpan(0, 120), ClockSpan(180, 1440)),
                ClockSpan(120, 180),
            ),
            ("America/New_York", 11, 1, 25, (ClockSpan(0, 1440),), ClockSpan(60, 120)),
            (
                "Australia/Lord_Howe",
                10,
                4,
                23.5,
                (ClockSpan(0, 120), ClockSpan(150, 1440)),
                ClockSpan(120, 150),
            ),
            (
                "Australia/Lord_Howe",
                4,
                5,
                24.5,
                (ClockSpan(0, 1440),),
                ClockSpan(90, 120),
            ),
        )
        for name, month, date, hours, spans, change in cases:
            with self.subTest(zone=name, month=month):
                zone = ZoneInfo(name)
                start = datetime(2026, month, date, tzinfo=zone).astimezone(UTC)
                end = datetime(2026, month, date + 1, tzinfo=zone).astimezone(UTC)
                timeline = VoiceTimeline(
                    (RoomInterval(1, 10, start, end, (human(),)),),
                    (),
                    (ObservationInterval(1, start, end),),
                )
                result = build_activity_detail(timeline, 1, 1, end, zone)
                day = result.days[-2]
                self.assertEqual(day.voice_seconds, hours * 3600)
                self.assertEqual(day.possible_seconds, hours * 3600)
                self.assertEqual(day.voice_spans, spans)
                self.assertEqual(day.clock_change_spans, (change,))
                self.assertEqual(day.missing_spans, ())
                self.assertEqual(day.future_spans, ())
                self.assertEqual(result.days[-1].future_spans, (ClockSpan(0, 1440),))

    def test_repeated_hour_unions_clock_positions_without_losing_elapsed_time(
        self,
    ) -> None:
        zone = ZoneInfo("America/New_York")
        first = datetime(2026, 11, 1, 1, 15, tzinfo=zone).astimezone(UTC)
        second = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1).astimezone(UTC)
        end = datetime(2026, 11, 1, 3, tzinfo=zone).astimezone(UTC)
        timeline = VoiceTimeline(
            tuple(
                RoomInterval(1, 10, start, start + timedelta(minutes=15), (human(),))
                for start in (first, second)
            ),
            (),
            (ObservationInterval(1, first, end),),
        )
        day = build_activity_detail(timeline, 1, 1, end, zone).days[-1]
        self.assertEqual(day.voice_spans, (ClockSpan(75, 90),))
        self.assertEqual(day.voice_seconds, 1800)
        self.assertEqual(day.clock_change_spans, (ClockSpan(60, 120),))

    def test_partial_day_uses_fractional_timezone_offset(self) -> None:
        zone = ZoneInfo("Asia/Kathmandu")
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(30, VoiceCheckpoint()),
            ]
        )
        day = build_activity_detail(timeline, 1, 1, at(30), zone).days[-1]
        self.assertEqual(day.voice_spans, (ClockSpan(345, 345.5),))
        self.assertEqual(day.missing_spans, (ClockSpan(0, 345),))
        self.assertEqual(day.future_spans, (ClockSpan(345.5, 1440),))
        self.assertEqual(day.voice_seconds, 30)
