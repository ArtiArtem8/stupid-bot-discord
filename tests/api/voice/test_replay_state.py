"""Incremental partitions preserve canonical history and exact analytics."""

import unittest
from dataclasses import replace

from api.voice.metrics.companions import companions
from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
)
from api.voice.timeline import (
    RoomInterval,
    VoiceReplayState,
    _split_at_gaps,
    _split_rooms,
    build_timeline,
)
from tests.api.voice.examples import at, human, record


class TestReplayState(unittest.TestCase):
    def test_partitions_preserve_snapshots_and_exact_metrics(self) -> None:
        facts = [record(0, VoiceSnapshot((human(),)))]
        for second in range(1, 70):
            state = replace(human(2), self_mute=None if second % 3 else True)
            fact = VoiceObservation(state) if second % 4 == 0 else VoiceCheckpoint()
            facts.append(record(second, fact))
        facts.extend(
            [
                record(70, VoiceSnapshot(())),
                record(71, ObservationGap(at(5), at(30), GapReason.WRITE_FAILURE, 1)),
                record(71, VoiceCheckpoint()),
                record(72, VoiceSnapshot((human(), human(2)))),
                record(73, VoiceLifecycle(stopped=True), guild=None),
                record(74, VoiceSnapshot((human(),)), boot="two", sequence=0),
                record(75, VoiceCheckpoint(), boot="two", sequence=1),
                record(74.5, VoiceCheckpoint(), boot="two", sequence=2),
                record(76, VoiceSnapshot((human(),)), boot="two", sequence=3),
            ]
        )
        policy = VoiceXpPolicy()
        for seed in range(12):
            replay = VoiceReplayState(())
            offset = 0
            while offset < len(facts):
                end = min(len(facts), offset + 1 + (seed * 7 + offset * 3) % 11)
                old = replay.snapshot()
                frozen = repr(old)
                if not replay.apply_many(facts[offset:end]):
                    replay = VoiceReplayState(facts[:end])
                actual = replay.snapshot()
                expected = build_timeline(facts[:end])
                self.assertEqual(actual, expected)
                self.assertEqual(repr(old), frozen)
                self.assertEqual(presence(actual, 1), presence(expected, 1))
                self.assertEqual(companions(actual, 1), companions(expected, 1))
                self.assertEqual(policy.explain(actual, 1), policy.explain(expected, 1))
                offset = end

    def test_snapshot_does_not_finalize_live_checkpoint_tail(self) -> None:
        state = VoiceReplayState([record(0, VoiceSnapshot((human(),)))])
        self.assertTrue(state.apply_many([record(10, VoiceCheckpoint())]))
        old = state.snapshot()
        self.assertEqual(presence(old, 1).total_seconds, 10)
        self.assertTrue(state.apply_many([record(20, VoiceCheckpoint())]))
        new = state.snapshot()
        self.assertEqual(presence(old, 1).total_seconds, 10)
        self.assertEqual(presence(new, 1).total_seconds, 20)
        self.assertEqual(len(new.rooms), 1)

    def test_unsafe_batch_is_rejected_before_any_mutation(self) -> None:
        initial = [record(0, VoiceSnapshot((human(),)))]
        state = VoiceReplayState(initial)
        before = state.snapshot()
        self.assertFalse(
            state.apply_many(
                [
                    record(5, VoiceObservation(human(2))),
                    record(4, VoiceCheckpoint()),
                ]
            )
        )
        self.assertEqual(state.snapshot(), before)

    def test_gap_candidates_preserve_open_overlapping_and_zero_metadata(self) -> None:
        rooms = [
            RoomInterval(1, 10, at(n), at(n + 10), (human(),))
            for n in range(0, 100, 10)
        ]
        for trial in range(100):
            gaps: list[ObservationGap] = []
            for index in range(20):
                start = (trial * 37 + index * 17) % 120 - 10
                end = (
                    None
                    if trial % 3 == 0
                    else at(start + (trial * 13 + index * 7) % 50)
                )
                gaps.append(ObservationGap(at(start), end, GapReason.UNKNOWN, 1))
            gaps.reverse()
            expected = [part for room in rooms for part in _split_at_gaps(room, gaps)]
            self.assertEqual(list(_split_rooms(rooms, gaps)), expected)
