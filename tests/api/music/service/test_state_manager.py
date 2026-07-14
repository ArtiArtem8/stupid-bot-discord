"""Tests for music state manager sessions and timers.
Covers session lifecycle, timer control, and expiry detection.
"""

import time
import unittest
from typing import override

import mafic

from api.music.models import PlaybackAttempt
from api.music.service.state_manager import StateManager
from tests.api.music.helpers import make_entry


class TestStateManager(unittest.TestCase):
    @override
    def setUp(self):
        self.manager = StateManager()

    def test_get_or_create_session(self):
        session = self.manager.get_or_create_session(123)
        self.assertEqual(session.guild_id, 123)
        self.assertEqual(self.manager.get_session(123), session)

        session2 = self.manager.get_or_create_session(123)
        self.assertIs(session2, session)

    def test_end_session(self):
        self.manager.get_or_create_session(123)
        attempt = PlaybackAttempt(1, make_entry("track"))
        self.manager.record_track_start(123, attempt)

        sess = self.manager.end_session(123)
        if sess is None:
            self.fail("expected session to be returned before removal")
        self.assertEqual(sess.guild_id, 123)
        self.assertIsNone(self.manager.get_session(123))
        self.assertNotIn((123, attempt.attempt_id), self.manager._track_start_times_dt)

    def test_timers(self):
        self.manager.start_timer(123, "empty")
        self.assertTrue(self.manager.is_timer_active(123))
        self.assertIn(123, self.manager.empty_channel_timers)

        self.manager.cancel_timer(123)
        self.assertFalse(self.manager.is_timer_active(123))

    def test_late_end_preserves_new_attempt_start_and_records_old_requester(self):
        first = PlaybackAttempt(
            1,
            make_entry("same", entry_id=1, requester_id=10),
        )
        second = PlaybackAttempt(
            2,
            make_entry("same", entry_id=2, requester_id=20),
        )
        self.manager.record_track_start(123, first)
        self.manager.record_track_start(123, second)

        self.manager.record_history(123, first, mafic.EndReason.STOPPED)

        session = self.manager.get_session(123)
        if session is None:
            self.fail("expected active session")
        self.assertEqual(session.tracks[-1].requester_id, 10)
        self.assertIn((123, second.attempt_id), self.manager._track_start_times_dt)

    def test_cleanup_history_is_marked_skipped(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("cleanup", requester_id=10))
        self.manager.record_track_start(123, attempt)

        self.manager.record_history(123, attempt, mafic.EndReason.CLEANUP)

        session = self.manager.get_session(123)
        if session is None:
            self.fail("expected active session")
        self.assertTrue(session.tracks[-1].skipped)

    def test_get_expired_timers(self):
        self.manager.start_timer(123, "test")
        self.manager.empty_channel_timers[123]["timestamp"] = time.monotonic() - 100

        expired = self.manager.get_expired_timers(timeout_duration=50)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0][0], 123)

        not_expired = self.manager.get_expired_timers(timeout_duration=200)
        self.assertEqual(len(not_expired), 0)
