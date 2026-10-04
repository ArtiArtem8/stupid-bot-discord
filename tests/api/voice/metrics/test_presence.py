import unittest
from dataclasses import replace

from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceObservation,
    VoiceSnapshot,
)
from api.voice.profile.build import build_profile
from api.voice.profile.details import build_activity_detail
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import build_timeline
from tests.api.voice.examples import at, example, human, record


class TestPresence(unittest.TestCase):
    def test_transport_session_changes_preserve_a_contiguous_visit(self) -> None:
        initial = replace(human(), session_id="a")
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((initial,))),
                record(10, VoiceObservation(replace(initial, session_id="b"))),
                record(20, VoiceObservation(replace(initial, session_id=None))),
                record(
                    30,
                    VoiceObservation(replace(initial, session_id="c", channel_id=20)),
                ),
                record(60, VoiceCheckpoint()),
            ]
        )
        result = presence(timeline, 1)
        self.assertEqual(result.total_seconds, 60)
        self.assertEqual(result.session_count, 1)
        self.assertEqual(result.average_session_seconds, 60)
        self.assertEqual(result.median_session_seconds, 60)

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

    def test_short_return_keeps_visit_without_counting_absent_time(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((replace(human(), session_id="a"),))),
                record(10, VoiceObservation(human(1, None))),
                record(20, VoiceObservation(replace(human(), session_id="b"))),
                record(40, VoiceCheckpoint()),
            ]
        )
        result = presence(timeline, 1)
        self.assertEqual(result.session_count, 1)
        self.assertEqual(result.total_seconds, 30)
        self.assertEqual(result.average_session_seconds, 30)
        self.assertEqual(result.median_session_seconds, 30)
        self.assertEqual(build_profile(timeline, 1, 1, "UTC").session_count, 1)
        self.assertEqual(build_activity_detail(timeline, 1, 1, at(40)).presence, result)

    def test_five_minute_boundary_and_session_durations(self) -> None:
        for pause, count in ((299.999, 1), (300, 2), (301, 2)):
            with self.subTest(pause=pause):
                timeline = build_timeline(
                    [
                        record(0, VoiceSnapshot((human(),))),
                        record(1200, VoiceObservation(human(1, None))),
                        record(1200 + pause, VoiceObservation(human())),
                        record(3600 + pause, VoiceCheckpoint()),
                    ]
                )
                result = presence(timeline, 1)
                self.assertEqual(result.session_count, count)
                self.assertEqual(result.total_seconds, 3600)
                self.assertEqual(result.average_session_seconds, 3600 / count)
                self.assertEqual(result.median_session_seconds, 3600 / count)

    def test_short_observation_gap_still_splits_visits(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(
                    60,
                    ObservationGap(at(60), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                record(120, VoiceSnapshot((human(),))),
                record(180, VoiceCheckpoint()),
            ]
        )
        result = presence(timeline, 1)
        self.assertEqual(result.session_count, 2)
        self.assertEqual(result.total_seconds, 120)

    def test_midnight_and_short_absences_do_not_split_a_visit(self) -> None:
        timeline = build_timeline(
            [
                record(86340, VoiceSnapshot((human(),))),
                record(86400, VoiceCheckpoint()),
                record(86460, VoiceObservation(human(1, None))),
                record(86520, VoiceObservation(human())),
                record(86580, VoiceCheckpoint()),
            ]
        )
        result = presence(timeline, 1)
        self.assertEqual(result.session_count, 1)
        self.assertEqual(result.total_seconds, 180)
        clipped = presence(
            timeline, 1, VoiceScope(1, time_range=TimeRange(at(86400), at(86580)))
        )
        self.assertEqual(clipped.session_count, 1)
        self.assertEqual(clipped.average_session_seconds, 120)
