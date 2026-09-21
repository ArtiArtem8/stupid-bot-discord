import unittest

from api.voice.queries import user_summary
from tests.api.voice.examples import example


class TestQueries(unittest.TestCase):
    def test_summary_reuses_timeline_for_multiple_metrics_without_discord(self) -> None:
        timeline = example()
        summary = user_summary(timeline, 1)
        self.assertEqual(summary.presence.total_seconds, 1200)
        self.assertEqual(sum(summary.activity.hourly_seconds), 1200)
        self.assertEqual(summary.companions[0].shared_seconds, 600)
        self.assertEqual(summary.xp, 20)
        self.assertEqual(summary.xp_policy_version, "voice-v1")
        self.assertEqual(user_summary(timeline, 1), summary)
