"""Tests for explicit playable voice connection outcomes."""

import unittest
from dataclasses import FrozenInstanceError

from api.music.models import (
    MusicResultStatus,
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
