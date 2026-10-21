"""Queue-entry identity and enqueue results."""

import unittest
from dataclasses import FrozenInstanceError, replace

from stupidbot.features.music.playback.models import (
    EnqueueOutcome,
    PlaybackAttempt,
    QueueEntry,
    TrackRequester,
)
from tests.features.music.fixtures import make_track


class TestPlaybackIdentityModels(unittest.TestCase):
    def test_queue_entry_and_attempt_are_immutable_and_distinct(self) -> None:
        requester = TrackRequester(1, 2)
        entry = QueueEntry(3, make_track("track"), requester)
        attempt = PlaybackAttempt(4, entry)

        self.assertEqual(attempt.attempt_id, 4)
        self.assertIs(attempt.entry, entry)
        self.assertFalse(hasattr(entry, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            entry.__setattr__("entry_id", 5)

    def test_started_from_enqueue_is_false_without_attempt(self) -> None:
        entry = QueueEntry(1, make_track("track"), TrackRequester(1))

        outcome = EnqueueOutcome((entry,), None)

        self.assertFalse(outcome.started_from_enqueue)

    def test_started_from_enqueue_recognizes_same_entry_object(self) -> None:
        entry = QueueEntry(1, make_track("track"), TrackRequester(1))

        outcome = EnqueueOutcome((entry,), PlaybackAttempt(1, entry))

        self.assertTrue(outcome.started_from_enqueue)

    def test_started_from_enqueue_rejects_entry_outside_operation(self) -> None:
        created = QueueEntry(1, make_track("created"), TrackRequester(1))
        existing = QueueEntry(2, make_track("existing"), TrackRequester(2))

        outcome = EnqueueOutcome((created,), PlaybackAttempt(1, existing))

        self.assertFalse(outcome.started_from_enqueue)

    def test_started_from_enqueue_ignores_equal_lookalike_entry(self) -> None:
        track = make_track("same")
        requester = TrackRequester(1)
        expected = QueueEntry(1, track, requester)
        lookalike = replace(expected)
        self.assertEqual(lookalike, expected)

        outcome = EnqueueOutcome((expected,), PlaybackAttempt(1, lookalike))

        self.assertFalse(outcome.started_from_enqueue)

    def test_enqueue_outcome_reports_whether_entries_remain_waiting(self) -> None:
        first = QueueEntry(1, make_track("first"), TrackRequester(1))
        second = QueueEntry(2, make_track("second"), TrackRequester(1))
        existing = QueueEntry(3, make_track("existing"), TrackRequester(2))

        self.assertFalse(EnqueueOutcome((), None).has_waiting_entries)
        self.assertTrue(EnqueueOutcome((first,), None).has_waiting_entries)
        self.assertFalse(
            EnqueueOutcome(
                (first,),
                PlaybackAttempt(1, first),
            ).has_waiting_entries
        )
        self.assertTrue(
            EnqueueOutcome(
                (first,),
                PlaybackAttempt(1, existing),
            ).has_waiting_entries
        )
        self.assertTrue(
            EnqueueOutcome(
                (first, second),
                None,
            ).has_waiting_entries
        )
        self.assertTrue(
            EnqueueOutcome(
                (first, second),
                PlaybackAttempt(1, first),
            ).has_waiting_entries
        )
