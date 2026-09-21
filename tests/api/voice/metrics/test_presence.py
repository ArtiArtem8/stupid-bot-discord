import unittest
from dataclasses import replace

from api.voice.metrics.presence import presence
from api.voice.model import VoiceCheckpoint, VoiceObservation, VoiceSnapshot
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import build_timeline
from tests.api.voice.examples import at, example, human, record


class TestPresence(unittest.TestCase):
    def test_hand_computable_minutes_and_sessions(self) -> None:
        timeline = example()
        a, b, c = (presence(timeline, user) for user in (1, 2, 3))
        self.assertEqual(
            (a.total_seconds, b.total_seconds, c.total_seconds), (1200, 600, 600)
        )
        self.assertEqual((a.solo_seconds, a.group_seconds), (300, 900))
        self.assertEqual(a.session_count, 1)
        self.assertEqual(a.average_session_seconds, 1200)
        self.assertEqual(a.median_session_seconds, 1200)

    def test_global_guild_channel_and_clipped_scopes_are_consistent(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(10, VoiceObservation(human(1, None))),
                record(10, VoiceSnapshot((human(1, 20),)), guild=2, sequence=101),
                record(30, VoiceCheckpoint(), guild=None),
            ]
        )
        total = presence(timeline, 1).total_seconds
        self.assertEqual(
            total,
            sum(presence(timeline, 1, VoiceScope(g)).total_seconds for g in (1, 2)),
        )
        self.assertEqual(total, 30)
        self.assertEqual(
            presence(
                timeline, 1, VoiceScope(2, 20, TimeRange(at(15), at(25)))
            ).total_seconds,
            10,
        )
        self.assertEqual(presence(timeline, 1, VoiceScope(2, 10)).total_seconds, 0)

    def test_leave_and_new_discord_session_split_visits(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((replace(human(), session_id="a"),))),
                record(10, VoiceObservation(human(1, None))),
                record(20, VoiceObservation(replace(human(), session_id="b"))),
                record(40, VoiceCheckpoint()),
            ]
        )
        result = presence(timeline, 1)
        self.assertEqual(result.session_count, 2)
        self.assertEqual(result.average_session_seconds, 15)
        self.assertEqual(result.median_session_seconds, 15)
