import unittest

from stupidbot.features.voice.facts import (
    VoiceCheckpoint,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from stupidbot.features.voice.metrics.bots import bot_presence
from stupidbot.features.voice.metrics.companions import companions
from stupidbot.features.voice.metrics.presence import presence
from stupidbot.features.voice.timeline import build_timeline
from tests.features.voice.fixtures import human, record


class TestBots(unittest.TestCase):
    def test_bot_presence_is_distinct_from_human_and_counts_any_bot_once(self) -> None:
        timeline = build_timeline(
            [
                record(
                    0,
                    VoiceSnapshot(
                        (
                            human(),
                            VoiceStateSnapshot(8, 10, True),
                            VoiceStateSnapshot(9, 10, True),
                        )
                    ),
                ),
                record(600, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(bot_presence(timeline, 1).any_bot_seconds, 600)
        self.assertEqual(dict(bot_presence(timeline, 1).by_bot), {8: 600, 9: 600})
        self.assertEqual(presence(timeline, 8).total_seconds, 0)
        self.assertEqual(presence(timeline, 1).solo_seconds, 600)
        self.assertEqual(companions(timeline, 1), ())
