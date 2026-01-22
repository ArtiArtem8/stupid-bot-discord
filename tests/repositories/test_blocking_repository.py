"""Tests for blocking repository storage behaviors.
Covers CRUD, history roundtrips, and guild/user query paths.
"""

from __future__ import annotations

import copy
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import override

from api.blocking_models import BlockedUser, BlockHistoryEntry, NameHistoryEntry
from repositories.blocking_repository import (
    BlockingRepository,
)
from utils.json_types import JsonObject, JsonValue, is_json_object

type JsonDict = JsonObject


def _as_json_object(value: JsonValue) -> JsonObject:
    assert is_json_object(value)
    return value


class FakeAsyncJsonFileStore:
    """In-memory async store to avoid filesystem in tests."""

    def __init__(self, initial_data: JsonDict | None = None) -> None:
        self._data: JsonDict = copy.deepcopy(initial_data or {})
        self.update_calls = 0

    async def read(self) -> JsonDict:
        return copy.deepcopy(self._data)

    async def update(self, updater: Callable[[JsonDict], None]) -> None:
        self.update_calls += 1
        data = copy.deepcopy(self._data)
        updater(data)
        self._data = data

    @property
    def data(self) -> JsonDict:
        return copy.deepcopy(self._data)


class TestBlockingRepository(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.store = FakeAsyncJsonFileStore()
        self.repo = BlockingRepository(self.store)

    async def test_get_returns_none_when_user_not_found(self) -> None:
        result = await self.repo.get((123, 456))
        self.assertIsNone(result)

    async def test_get_returns_user_when_present(self) -> None:
        user = BlockedUser(
            user_id=456,
            current_username="user1",
            current_global_name="Global",
        )
        data = {
            "123": {
                "users": {
                    "456": user.to_dict(),
                }
            }
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        result = await self.repo.get((123, 456))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, BlockedUser)
        self.assertEqual(result.user_id, 456)
        self.assertEqual(result.current_username, "user1")
        self.assertEqual(result.current_global_name, "Global")

    async def test_get_ignores_invalid_guild_structure(self) -> None:
        data = {
            "123": "invalid",
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        result = await self.repo.get((123, 456))
        self.assertIsNone(result)

    async def test_get_all_flattens_all_guilds(self) -> None:
        user1 = BlockedUser(
            user_id=1,
            current_username="u1",
            current_global_name=None,
        )
        user2 = BlockedUser(
            user_id=2,
            current_username="u2",
            current_global_name="g2",
        )
        data = {
            "1": {
                "users": {
                    "1": user1.to_dict(),
                }
            },
            "2": {
                "users": {
                    "2": user2.to_dict(),
                }
            },
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        result = await self.repo.get_all()

        self.assertEqual(len(result), 2)
        ids = {u.user_id for u in result}
        self.assertSetEqual(ids, {1, 2})

    async def test_get_all_skips_non_dict_guild_values(self) -> None:
        user1 = BlockedUser(
            user_id=1,
            current_username="u1",
            current_global_name=None,
        )
        data = {
            "1": {
                "users": {
                    "1": user1.to_dict(),
                }
            },
            "2": "invalid",  # should be skipped
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        result = await self.repo.get_all()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].user_id, 1)

    async def test_save_requires_key(self) -> None:
        user = BlockedUser(
            user_id=42,
            current_username="test",
            current_global_name=None,
        )
        with self.assertRaises(ValueError):
            await self.repo.save(user)

    async def test_save_creates_guild_and_user(self) -> None:
        user = BlockedUser(
            user_id=42,
            current_username="test",
            current_global_name=None,
        )

        await self.repo.save(user, key=(1, 42))

        data = self.store.data
        self.assertIn("1", data)
        guild_data = _as_json_object(data["1"])
        self.assertIn("users", guild_data)
        users_map = _as_json_object(guild_data["users"])
        self.assertIn("42", users_map)
        stored = _as_json_object(users_map["42"])
        self.assertEqual(stored["user_id"], "42")
        self.assertEqual(stored["current_username"], "test")

    async def test_save_updates_existing_user(self) -> None:
        user = BlockedUser(
            user_id=42,
            current_username="old",
            current_global_name=None,
        )
        data = {
            "1": {
                "users": {
                    "42": user.to_dict(),
                }
            }
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        user.current_username = "new"
        await self.repo.save(user, key=(1, 42))

        updated_data = self.store.data
        guild_data = _as_json_object(updated_data["1"])
        users_map = _as_json_object(guild_data["users"])
        stored = _as_json_object(users_map["42"])
        self.assertEqual(stored["current_username"], "new")

    async def test_delete_removes_user(self) -> None:
        user = BlockedUser(
            user_id=42,
            current_username="test",
            current_global_name=None,
        )
        data = {
            "1": {
                "users": {
                    "42": user.to_dict(),
                }
            }
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        await self.repo.delete((1, 42))

        updated = self.store.data
        self.assertIn("1", updated)
        guild_data = _as_json_object(updated["1"])
        self.assertIn("users", guild_data)
        users_map = _as_json_object(guild_data["users"])
        self.assertNotIn("42", users_map)

    async def test_delete_nonexistent_user_is_noop(self) -> None:
        data: JsonDict = {
            "1": {
                "users": {},
            }
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        await self.repo.delete((1, 999))

        self.assertEqual(self.store.data, data)

    async def test_delete_nonexistent_guild_is_noop(self) -> None:
        data: JsonDict = {}
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        await self.repo.delete((1, 999))

        self.assertEqual(self.store.data, {})

    async def test_get_all_for_guild_returns_only_that_guild(self) -> None:
        user1 = BlockedUser(
            user_id=1,
            current_username="u1",
            current_global_name=None,
        )
        user2 = BlockedUser(
            user_id=2,
            current_username="u2",
            current_global_name=None,
        )
        data = {
            "1": {"users": {"1": user1.to_dict()}},
            "2": {"users": {"2": user2.to_dict()}},
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        users_g1 = await self.repo.get_all_for_guild(1)
        users_g2 = await self.repo.get_all_for_guild(2)

        self.assertEqual(len(users_g1), 1)
        self.assertEqual(users_g1[0].user_id, 1)
        self.assertEqual(len(users_g2), 1)
        self.assertEqual(users_g2[0].user_id, 2)

    async def test_get_all_for_guild_handles_missing_guild(self) -> None:
        data = {}
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        users = await self.repo.get_all_for_guild(123)
        self.assertEqual(users, [])

    async def test_get_all_for_guild_handles_invalid_guild_structure(self) -> None:
        data = {
            "123": "invalid",
        }
        self.store = FakeAsyncJsonFileStore(data)
        self.repo = BlockingRepository(self.store)

        users = await self.repo.get_all_for_guild(123)
        self.assertEqual(users, [])

    async def test_roundtrip_block_history_and_name_history(self) -> None:
        now = datetime.now(tz=timezone.utc)
        earlier = now - timedelta(days=1)

        user = BlockedUser(
            user_id=42,
            current_username="u",
            current_global_name=None,
            block_history=[
                BlockHistoryEntry(
                    admin_id=1,
                    reason="r1",
                    timestamp=earlier,
                )
            ],
            unblock_history=[],
            name_history=[
                NameHistoryEntry(
                    username="old",
                    timestamp=earlier,
                )
            ],
            blocked=True,
        )

        await self.repo.save(user, key=(10, 42))
        loaded = await self.repo.get((10, 42))

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.user_id, 42)
        self.assertTrue(loaded.block_history)
        self.assertEqual(loaded.block_history[0].admin_id, 1)
        self.assertEqual(loaded.name_history[0].username, "old")
        self.assertTrue(loaded.is_blocked)


class TestBlockingRepositoryWithRealData(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.raw_data_fixture: JsonDict = {
            "111111111111111111": {
                "users": {
                    "222222222222222222": {
                        "block_history": [
                            {
                                "admin_id": "999999999999999999",
                                "reason": "violation_a",
                                "timestamp": "2024-01-01T10:00:00.000000+00:00",
                            }
                        ],
                        "blocked": True,
                        "current_global_name": "UserOneGlobal",
                        "current_username": "user_one",
                        "name_history": [
                            {
                                "timestamp": "2024-01-01T10:00:00.000000+00:00",
                                "username": "user_one_old",
                            }
                        ],
                        "unblock_history": [],
                        "user_id": "222222222222222222",
                    },
                    "333333333333333333": {
                        "block_history": [
                            {
                                "admin_id": "999999999999999999",
                                "reason": "violation_b",
                                "timestamp": "2024-02-01T12:00:00.000000+00:00",
                            },
                            {
                                "admin_id": "888888888888888888",
                                "reason": "violation_c",
                                "timestamp": "2024-03-01T14:00:00.000000+00:00",
                            },
                        ],
                        "blocked": False,
                        "current_global_name": "UserTwoGlobal",
                        "current_username": "user_two",
                        "name_history": [],
                        "unblock_history": [
                            {
                                "admin_id": "999999999999999999",
                                "reason": "appeal_accepted",
                                "timestamp": "2024-02-02T12:00:00.000000+00:00",
                            },
                            {
                                "admin_id": "888888888888888888",
                                "reason": "amnesty",
                                "timestamp": "2024-03-05T10:00:00.000000+00:00",
                            },
                        ],
                        "user_id": "333333333333333333",
                    },
                }
            },
            "444444444444444444": {
                "users": {
                    "555555555555555555": {
                        "block_history": [],
                        "blocked": False,
                        "current_global_name": None,
                        "current_username": "user_three",
                        "name_history": [],
                        "unblock_history": [],
                        "user_id": "555555555555555555",
                    }
                }
            },
        }
        self.store = FakeAsyncJsonFileStore(self.raw_data_fixture)
        self.repo = BlockingRepository(self.store)

    async def test_get_existing_blocked_user(self) -> None:
        """Test retrieving a specific user that is currently blocked."""
        key = (111111111111111111, 222222222222222222)
        user = await self.repo.get(key)

        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.user_id, 222222222222222222)
        self.assertTrue(user.is_blocked)
        self.assertEqual(user.current_username, "user_one")
        self.assertEqual(len(user.block_history), 1)
        self.assertEqual(user.block_history[0].reason, "violation_a")

    async def test_get_existing_unblocked_user_with_history(self) -> None:
        """Test retrieving a user who was blocked but is now unblocked."""
        key = (111111111111111111, 333333333333333333)
        user = await self.repo.get(key)

        self.assertIsNotNone(user)
        assert user is not None
        self.assertFalse(user.is_blocked)
        self.assertEqual(len(user.block_history), 2)
        self.assertEqual(len(user.unblock_history), 2)
        self.assertEqual(user.unblock_history[1].reason, "amnesty")

    async def test_get_all_users_across_guilds(self) -> None:
        """Test that get_all retrieves users from all guilds correctly."""
        all_users = await self.repo.get_all()

        self.assertEqual(len(all_users), 3)

        user_ids = {u.user_id for u in all_users}
        expected_ids = {
            222222222222222222,
            333333333333333333,
            555555555555555555,
        }
        self.assertEqual(user_ids, expected_ids)

    async def test_get_all_for_specific_guild(self) -> None:
        """Test retrieving users for a single guild."""
        guild_id = 111111111111111111
        users = await self.repo.get_all_for_guild(guild_id)

        self.assertEqual(len(users), 2)
        user_ids = {u.user_id for u in users}
        self.assertIn(222222222222222222, user_ids)
        self.assertIn(333333333333333333, user_ids)

    async def test_save_new_user_to_existing_guild(self) -> None:
        """Test adding a completely new user to an existing guild structure."""
        guild_id = 111111111111111111
        new_user_id = 666666666666666666

        new_user = BlockedUser(
            user_id=new_user_id,
            current_username="new_guy",
            current_global_name="New Guy Global",
            blocked=True,
        )
        new_user.add_block_entry(admin_id=999999999999999999, reason="spam")

        await self.repo.save(new_user, key=(guild_id, new_user_id))

        data = await self.store.read()
        guild_data = _as_json_object(data[str(guild_id)])
        users_map = _as_json_object(guild_data["users"])
        saved_user_dict = _as_json_object(users_map[str(new_user_id)])

        self.assertEqual(saved_user_dict["current_username"], "new_guy")
        self.assertTrue(saved_user_dict["blocked"])
        self.assertEqual(len(saved_user_dict["block_history"]), 1)

    async def test_save_user_to_new_guild(self) -> None:
        """Test saving a user creates a new guild entry if it doesn't exist."""
        new_guild_id = 777777777777777777
        user_id = 888888888888888888

        user = BlockedUser(
            user_id=user_id, current_username="lonely_user", current_global_name=None
        )

        await self.repo.save(user, key=(new_guild_id, user_id))

        data = await self.store.read()
        self.assertIn(str(new_guild_id), data)
        guild_data = _as_json_object(data[str(new_guild_id)])
        users_map = _as_json_object(guild_data["users"])
        self.assertIn(str(user_id), users_map)

    async def test_data_integrity_after_save(self) -> None:
        """Ensure that saving a user doesn't corrupt other users in the guild."""
        guild_id = 111111111111111111
        user_id = 222222222222222222

        user = await self.repo.get((guild_id, user_id))
        assert user is not None
        user.current_username = "updated_name"
        await self.repo.save(user, key=(guild_id, user_id))

        data = await self.store.read()
        guild_data = _as_json_object(data[str(guild_id)])
        guild_users = _as_json_object(guild_data["users"])

        updated_user = _as_json_object(guild_users[str(user_id)])
        self.assertEqual(updated_user["current_username"], "updated_name")

        other_user_id = "333333333333333333"
        self.assertIn(other_user_id, guild_users)
        other_user = _as_json_object(guild_users[other_user_id])
        self.assertEqual(other_user["current_username"], "user_two")

    async def test_missing_fields_defaults(self) -> None:
        """Test handling of users with missing optional fields."""
        incomplete_data: JsonDict = {
            "999": {
                "users": {
                    "100": {
                        "user_id": "100",
                    }
                }
            }
        }
        store = FakeAsyncJsonFileStore(incomplete_data)
        repo = BlockingRepository(store)

        user = await repo.get((999, 100))

        self.assertIsNone(user)


if __name__ == "__main__":
    unittest.main()
