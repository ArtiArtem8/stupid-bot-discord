import unittest
from datetime import timedelta

from api.voice.metrics.activity import activity
from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import build_timeline
from tests.api.voice.examples import START, human, record


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
