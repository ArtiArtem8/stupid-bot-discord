import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from struct import pack
from zoneinfo import ZoneInfo

from stupidbot.features.voice.facts import VoiceCheckpoint, VoiceSnapshot
from stupidbot.features.voice.metrics.activity import activity
from stupidbot.features.voice.scope import TimeRange, VoiceScope
from stupidbot.features.voice.timeline import build_timeline
from tests.features.voice.fixtures import START, human, record


def new_york_2026() -> ZoneInfo:
    # Minimal TZif fixture with two real 2026 transitions. Windows need not
    # have a system IANA database, and production gains no tzdata dependency.
    spring = int(datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp())
    autumn = int(datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp())
    payload = (
        b"TZif\0"
        + b"\0" * 15
        + pack(">6l", 0, 0, 0, 2, 2, 8)
        + pack(">2l", spring, autumn)
        + bytes((1, 0))
        + pack(">lbb", -18000, 0, 0)
        + pack(">lbb", -14400, 1, 4)
        + b"EST\0EDT\0"
    )
    return ZoneInfo.from_file(BytesIO(payload), key="Test/New_York_2026")


class TestActivity(unittest.TestCase):
    def test_midnight_splits_daily_weekday_and_hourly_buckets(self) -> None:
        timeline = build_timeline(
            [
                record(23 * 3600 + 30 * 60, VoiceSnapshot((human(),))),
                record(24 * 3600 + 30 * 60, VoiceCheckpoint()),
            ]
        )
        result = activity(timeline, 1)
        self.assertEqual(len(result.hourly_seconds), 24)
        self.assertEqual(result.hourly_seconds[23], 1800)
        self.assertEqual(result.hourly_seconds[0], 1800)
        self.assertEqual(sum(result.hourly_seconds), 3600)
        self.assertEqual(result.weekday_seconds[:2], (1800, 1800))
        self.assertEqual(
            dict(result.daily_seconds),
            {START.date(): 1800, (START + timedelta(days=1)).date(): 1800},
        )

    def test_calendar_uses_supplied_timezone_instead_of_utc(self) -> None:
        timeline = build_timeline(
            [record(0, VoiceSnapshot((human(),))), record(3600, VoiceCheckpoint())]
        )
        result = activity(
            timeline, 1, timezone=timezone(timedelta(hours=-3, minutes=-30))
        )
        self.assertEqual(result.hourly_seconds[20:22], (1800, 1800))
        self.assertEqual(result.weekday_seconds[6], 3600)
        self.assertEqual(
            dict(result.daily_seconds), {(START - timedelta(days=1)).date(): 3600}
        )
        self.assertEqual(activity(timeline, 1).hourly_seconds[0], 3600)

    def test_fractional_offset_splits_at_local_midnight(self) -> None:
        timeline = build_timeline(
            [record(0, VoiceSnapshot((human(),))), record(3600, VoiceCheckpoint())]
        )
        result = activity(timeline, 1, timezone=timezone(timedelta(minutes=-30)))
        self.assertEqual(result.hourly_seconds[23], 1800)
        self.assertEqual(result.hourly_seconds[0], 1800)
        self.assertEqual(len(result.daily_seconds), 2)

    def test_dst_spring_skips_hour_without_losing_elapsed_seconds(self) -> None:
        start = datetime(2026, 3, 8, 6, tzinfo=UTC)
        timeline = build_timeline(
            [
                replace(record(0, VoiceSnapshot((human(),))), observed_at=start),
                replace(
                    record(10800, VoiceCheckpoint()),
                    observed_at=start + timedelta(hours=3),
                ),
            ]
        )
        result = activity(timeline, 1, timezone=new_york_2026())
        self.assertEqual(result.hourly_seconds[1:5], (3600, 0, 3600, 3600))
        self.assertEqual(sum(result.hourly_seconds), 10800)

    def test_dst_autumn_counts_repeated_hour_twice_in_the_same_bucket(self) -> None:
        start = datetime(2026, 11, 1, 5, tzinfo=UTC)
        timeline = build_timeline(
            [
                replace(record(0, VoiceSnapshot((human(),))), observed_at=start),
                replace(
                    record(10800, VoiceCheckpoint()),
                    observed_at=start + timedelta(hours=3),
                ),
            ]
        )
        result = activity(timeline, 1, timezone=new_york_2026())
        self.assertEqual(result.hourly_seconds[1:3], (7200, 3600))
        self.assertEqual(sum(result.hourly_seconds), 10800)

    def test_offset_changes_split_hours_without_moving_time_between_buckets(
        self,
    ) -> None:
        cases = (
            (
                "Pacific/Chatham",
                datetime(2026, 9, 26, 12, 30, tzinfo=UTC),
                {1: 2700, 2: 2700, 3: 900, 4: 900},
            ),
            (
                "Pacific/Chatham",
                datetime(2026, 4, 4, 12, 30, tzinfo=UTC),
                {2: 3600, 3: 3600},
            ),
            (
                "Antarctica/Troll",
                datetime(2026, 10, 24, 23, 30, tzinfo=UTC),
                {1: 3600, 2: 3600},
            ),
        )
        for name, start, expected in cases:
            with self.subTest(zone=name, start=start):
                zone = ZoneInfo(name)
                timeline = build_timeline(
                    [
                        replace(
                            record(0, VoiceSnapshot((human(),))), observed_at=start
                        ),
                        replace(
                            record(7200, VoiceCheckpoint()),
                            observed_at=start + timedelta(hours=2),
                        ),
                    ]
                )
                result = activity(timeline, 1, timezone=zone)
                self.assertEqual(
                    result.hourly_seconds,
                    tuple(expected.get(hour, 0) for hour in range(24)),
                )
                self.assertEqual(sum(result.hourly_seconds), 7200)
                day = start.astimezone(zone).date()
                self.assertEqual(result.daily_seconds, ((day, 7200),))
                self.assertEqual(
                    result.weekday_seconds,
                    tuple(
                        7200 if weekday == day.weekday() else 0 for weekday in range(7)
                    ),
                )

    def test_query_end_and_start_at_transition_keep_half_open_hour_buckets(
        self,
    ) -> None:
        cases = (
            ("Pacific/Chatham", datetime(2026, 9, 26, 14, tzinfo=UTC), 2, 3),
            ("Pacific/Chatham", datetime(2026, 4, 4, 14, tzinfo=UTC), 3, 2),
            ("Antarctica/Troll", datetime(2026, 10, 25, 1, tzinfo=UTC), 2, 1),
        )
        for name, transition, before_hour, after_hour in cases:
            zone = ZoneInfo(name)
            start = transition - timedelta(minutes=45)
            end = transition + timedelta(minutes=15)
            timeline = build_timeline(
                [
                    replace(record(0, VoiceSnapshot((human(),))), observed_at=start),
                    replace(record(3600, VoiceCheckpoint()), observed_at=end),
                ]
            )
            for window, hour, seconds in (
                (TimeRange(start, transition), before_hour, 2700),
                (TimeRange(transition, end), after_hour, 900),
            ):
                with self.subTest(zone=name, transition=transition, window=window):
                    result = activity(
                        timeline,
                        1,
                        VoiceScope(1, time_range=window),
                        timezone=zone,
                    )
                    self.assertEqual(
                        result.hourly_seconds,
                        tuple(seconds if value == hour else 0 for value in range(24)),
                    )
