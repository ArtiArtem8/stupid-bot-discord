import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from struct import pack
from zoneinfo import ZoneInfo

from api.voice.metrics.activity import activity
from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import build_timeline
from tests.api.voice.examples import START, human, record


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
