"""Tests for blocked-user model behavior.
Covers history updates, timestamping, and serialization roundtrips.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from api.blocking_models import BlockedUser


class TestBlockingModels(unittest.TestCase):
    def test_add_block_entry_sets_blocked_and_appends_history(self) -> None:
        fixed = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with patch("api.blocking_models.utcnow", return_value=fixed):
            user = BlockedUser(
                user_id=1,
                current_username="u",
                current_global_name=None,
                blocked=False,
            )
            user.add_block_entry(admin_id=123, reason="spam")

        self.assertTrue(user.blocked)
        self.assertEqual(len(user.block_history), 1)
        self.assertEqual(user.block_history[0].admin_id, 123)
        self.assertEqual(user.block_history[0].reason, "spam")
        self.assertEqual(user.block_history[0].timestamp, fixed)

    def test_update_name_history_returns_false_when_no_change(self) -> None:
        user = BlockedUser(
            user_id=1,
            current_username="same",
            current_global_name="global",
            blocked=False,
        )
        changed = user.update_name_history(username="same", global_name=None)
        self.assertFalse(changed)
        self.assertEqual(len(user.name_history), 0)

    def test_update_name_history_updates_when_username_changes(self) -> None:
        fixed = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with patch("api.blocking_models.utcnow", return_value=fixed):
            user = BlockedUser(
                user_id=1,
                current_username="old",
                current_global_name="global",
                blocked=False,
            )
            changed = user.update_name_history(username="new", global_name="global")

        self.assertTrue(changed)
        self.assertEqual(user.current_username, "new")
        self.assertEqual(len(user.name_history), 1)
        self.assertEqual(user.name_history[0].username, "new")
        self.assertEqual(user.name_history[0].timestamp, fixed)

    def test_to_dict_from_dict_roundtrip(self) -> None:
        user = BlockedUser(
            user_id=5,
            current_username="name",
            current_global_name="g",
            blocked=True,
        )
        payload = user.to_dict()
        restored = BlockedUser.from_dict(payload)

        self.assertEqual(restored.user_id, 5)
        self.assertEqual(restored.current_username, "name")
        self.assertEqual(restored.current_global_name, "g")
        self.assertTrue(restored.blocked)
