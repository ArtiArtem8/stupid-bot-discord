import unittest

from api.voice.metrics.companions import companions
from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import build_timeline
from tests.api.voice.examples import example, human, record


class TestCompanions(unittest.TestCase):
    def test_pair_overlap_is_not_doubled(self) -> None:
        timeline = example()
        a = {stat.user_id: stat for stat in companions(timeline, 1)}
        b = {stat.user_id: stat for stat in companions(timeline, 2)}
        self.assertEqual(a[2].shared_seconds, 600)
        self.assertEqual(a[3].shared_seconds, 600)
        self.assertEqual(b[3].shared_seconds, 300)
        self.assertEqual(a[2].private_seconds, 300)
        self.assertEqual(a[3].private_seconds, 300)

    def test_different_channels_do_not_overlap(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(), human(2, 20)))),
                record(600, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(companions(timeline, 1), ())
