"""Tests for music state manager sessions and timers.
Covers session lifecycle, timer control, and expiry detection.
"""

import time
import unittest
from typing import override

from api.music.service.state_manager import StateManager


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
        self.manager.record_track_start(123)

        sess = self.manager.end_session(123)
        self.assertIsNotNone(sess)
        self.assertEqual(sess.guild_id, 123)
        self.assertIsNone(self.manager.get_session(123))
        self.assertNotIn(123, self.manager._track_start_times_dt)

    def test_timers(self):
        self.manager.start_timer(123, "empty")
        self.assertTrue(self.manager.is_timer_active(123))
        self.assertIn(123, self.manager.empty_channel_timers)

        self.manager.cancel_timer(123)
        self.assertFalse(self.manager.is_timer_active(123))

    def test_get_expired_timers(self):
        self.manager.start_timer(123, "test")
        self.manager.empty_channel_timers[123]["timestamp"] = time.monotonic() - 100

        expired = self.manager.get_expired_timers(timeout_duration=50)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0][0], 123)

        not_expired = self.manager.get_expired_timers(timeout_duration=200)
        self.assertEqual(len(not_expired), 0)
