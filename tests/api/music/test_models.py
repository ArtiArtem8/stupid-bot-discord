"""Tests for explicit playable voice connection outcomes."""

import unittest
from dataclasses import FrozenInstanceError

from api.music.models import (
    EnqueueOutcome,
    MusicResultStatus,
    MusicSession,
    PlaybackAttempt,
    QueueEntry,
    TrackRequester,
    VoiceCheckResult,
)
from tests.api.music.helpers import make_track


class TestVoiceCheckResult(unittest.TestCase):
    def test_connected_results_are_successful(self) -> None:
        for result in (
            VoiceCheckResult.SUCCESS,
            VoiceCheckResult.ALREADY_CONNECTED,
            VoiceCheckResult.MOVED_CHANNELS,
        ):
            with self.subTest(result=result):
                self.assertIs(result.status, MusicResultStatus.SUCCESS)

    def test_failed_connection_results_do_not_allow_playback(self) -> None:
        for result in (
            VoiceCheckResult.TIMEOUT,
            VoiceCheckResult.CONNECTION_FAILED,
            VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE,
        ):
            with self.subTest(result=result):
                self.assertIsNot(result.status, MusicResultStatus.SUCCESS)

    def test_music_service_unavailable_is_failure_not_error(self) -> None:
        self.assertIs(
            VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE.status,
            MusicResultStatus.FAILURE,
        )


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
        lookalike = QueueEntry(1, track, requester)
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


class TestMusicSession(unittest.TestCase):
    def test_track_without_requester_does_not_add_participant_zero(self) -> None:
        session = MusicSession(guild_id=1)

        session.add_track(
            title="Track",
            uri="https://example.com/track",
            requester_id=None,
            channel_id=None,
        )

        self.assertEqual(session.participants, set())
