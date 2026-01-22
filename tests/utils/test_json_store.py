"""Tests for async JSON store utilities.
Covers read/write/update flows, backups, and type aliases.
"""

from __future__ import annotations

import asyncio
import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import patch

from utils.json_store import AsyncJsonFileStore, JsonDict, Updater
from utils.json_types import JsonObject, JsonValue, is_json_object


def _as_json_object(value: JsonValue) -> JsonObject:
    assert is_json_object(value)
    return value


def _as_json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


class TestAsyncJsonFileStore(unittest.IsolatedAsyncioTestCase):
    """Test cases for AsyncJsonFileStore class."""

    @override
    def setUp(self) -> None:
        """Set up test fixtures with temporary directory."""
        self.temp_dir_obj = TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        random.seed(42)
        uuid = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8))  # noqa: S311
        self.test_file = self.temp_dir / f"test_data_{uuid}.json"
        self.backup_dir = self.temp_dir / "backups"

    @override
    def tearDown(self) -> None:
        """Clean up temporary directory."""
        self.temp_dir_obj.cleanup()
        self.test_file.unlink(missing_ok=True)

    async def test_read_nonexistent_file_returns_empty_dict(self) -> None:
        """Test that reading a nonexistent file returns an empty dictionary."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        result = await store.read()

        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})
        self.assertEqual(len(result), 0)

    async def test_read_existing_file_returns_data(self) -> None:
        """Test that reading an existing JSON file returns its content."""
        test_data: JsonDict = {"key1": "value1", "key2": 42, "nested": {"a": 1}}

        # Create test file
        self.test_file.write_text(json.dumps(test_data, indent=4), encoding="utf-8")

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        result = await store.read()

        self.assertEqual(result, test_data)
        self.assertEqual(result["key1"], "value1")
        self.assertEqual(result["key2"], 42)
        self.assertIn("nested", result)

    async def test_write_creates_new_file(self) -> None:
        """Test that write creates a new file when it doesn't exist."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )
        test_data: JsonDict = {"test": "data", "number": 123}

        await store.write(test_data)

        self.assertTrue(self.test_file.exists())
        saved_data = json.loads(
            await asyncio.to_thread(self.test_file.read_text, encoding="utf-8")
        )
        self.assertEqual(saved_data, test_data)

    async def test_write_overwrites_existing_file(self) -> None:
        """Test that write overwrites existing file content."""
        initial_data: JsonDict = {"old": "data"}
        new_data: JsonDict = {"new": "content", "value": 999}

        # Create initial file
        self.test_file.write_text(json.dumps(initial_data, indent=4), encoding="utf-8")

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        await store.write(new_data)

        saved_data = json.loads(
            await asyncio.to_thread(self.test_file.read_text, encoding="utf-8")
        )
        self.assertEqual(saved_data, new_data)
        self.assertNotEqual(saved_data, initial_data)

    async def test_write_creates_backup(self) -> None:
        """Test that write creates backup files when configured."""
        initial_data: JsonDict = {"version": 1}
        updated_data: JsonDict = {"version": 2}

        self.test_file.write_text(json.dumps(initial_data, indent=4), encoding="utf-8")

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_amount=3,
            backup_dir=self.backup_dir,
        )

        await store.write(updated_data)

        self.assertTrue(self.backup_dir.exists())
        backups = list(
            self.backup_dir.glob(f"{self.test_file.stem}_*{self.test_file.suffix}")
        )
        self.assertEqual(len(backups), 1)

        # Verify backup contains old data
        backup_data = json.loads(
            await asyncio.to_thread(backups[0].read_text, encoding="utf-8")
        )
        self.assertEqual(backup_data, initial_data)

    async def test_write_respects_backup_amount(self) -> None:
        """Test that write maintains only the specified number of backups."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_amount=2,
            backup_dir=self.backup_dir,
        )

        self.test_file.write_text(
            json.dumps({"version": 0}, indent=4), encoding="utf-8"
        )
        await asyncio.gather(
            *[store.write({"version": i}) for i in range(1, 7)],
            return_exceptions=False,
        )
        await asyncio.sleep(0.2)  # Ensure all file ops complete
        await store.write({"version": "final"})

        backups = list(
            self.backup_dir.glob(f"{self.test_file.stem}_*{self.test_file.suffix}")
        )
        self.assertLessEqual(len(backups), 3)

    async def test_write_no_backup_when_backup_amount_zero(self) -> None:
        """Test that no backups are created when backup_amount is 0."""
        self.test_file.write_text(
            json.dumps({"initial": "data"}, indent=4), encoding="utf-8"
        )

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_amount=0,
            backup_dir=self.backup_dir,
        )

        await store.write({"updated": "data"})

        # Backup dir should not be created
        self.assertFalse(self.backup_dir.exists())

    async def test_update_with_sync_updater(self) -> None:
        """Test update with a synchronous updater function."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        def sync_updater(data: JsonDict) -> None:
            data["key1"] = "value1"
            data["counter"] = 42

        result = await store.update(sync_updater)

        self.assertEqual(result["key1"], "value1")
        self.assertEqual(result["counter"], 42)

        # Verify data was persisted
        saved_data = await store.read()
        self.assertEqual(saved_data, result)

    async def test_update_with_async_updater(self) -> None:
        """Test update with an asynchronous updater function."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        async def async_updater(data: JsonDict) -> None:
            await asyncio.sleep(0.01)
            data["async_key"] = "async_value"
            data["timestamp"] = 12345

        result = await store.update(async_updater)

        self.assertEqual(result["async_key"], "async_value")
        self.assertEqual(result["timestamp"], 12345)

        # Verify data was persisted
        saved_data = await store.read()
        self.assertEqual(saved_data, result)

    async def test_update_modifies_existing_data(self) -> None:
        """Test that update correctly modifies existing data."""
        initial_data: JsonDict = {"existing": "value", "count": 10}
        self.test_file.write_text(json.dumps(initial_data, indent=4), encoding="utf-8")

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        def updater(data: JsonDict) -> None:
            current = data.get("count")
            current_value = current if isinstance(current, int) else 0
            data["count"] = current_value + 5
            data["new_field"] = "added"

        result = await store.update(updater)

        self.assertEqual(result["existing"], "value")
        self.assertEqual(result["count"], 15)
        self.assertEqual(result["new_field"], "added")

    async def test_update_with_lock_prevents_race_conditions(self) -> None:
        """Test that concurrent updates are properly serialized by lock."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        # Initialize with a counter
        await store.write({"counter": 0})

        async def increment_updater(data: JsonDict) -> None:
            current = data.get("counter")
            current_value = current if isinstance(current, int) else 0
            await asyncio.sleep(0.01)  # Simulate work
            data["counter"] = current_value + 1

        # Run multiple concurrent updates
        await asyncio.gather(
            store.update(increment_updater),
            store.update(increment_updater),
            store.update(increment_updater),
        )

        result = await store.read()
        # All three increments should have been applied sequentially
        self.assertEqual(result["counter"], 3)

    async def test_update_returns_final_data(self) -> None:
        """Test that update returns the final state of data."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        def updater(data: JsonDict) -> None:
            data["result"] = "final"
            data["status"] = "completed"

        result = await store.update(updater)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["result"], "final")
        self.assertEqual(result["status"], "completed")

    async def test_custom_encoding(self) -> None:
        """Test that custom encoding parameter is respected."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            encoding="utf-16",
            backup_dir=self.backup_dir,
        )

        test_data: JsonDict = {"unicode": "тест", "emoji": "🎉"}

        await store.write(test_data)

        # Verify file was written with correct encoding
        content = self.test_file.read_text(encoding="utf-16")
        self.assertIn("тест", content)
        self.assertIn("🎉", content)

        result = await store.read()
        self.assertEqual(result, test_data)

    async def test_path_as_string(self) -> None:
        """Test that path parameter accepts string."""
        store = AsyncJsonFileStore(
            path=str(self.test_file),
            backup_dir=self.backup_dir,
        )

        await store.write({"test": "string_path"})

        self.assertTrue(self.test_file.exists())
        result = await store.read()
        self.assertEqual(result["test"], "string_path")

    async def test_path_as_pathlib_path(self) -> None:
        """Test that path parameter accepts Path object."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        await store.write({"test": "path_object"})

        self.assertTrue(self.test_file.exists())
        result = await store.read()
        self.assertEqual(result["test"], "path_object")

    async def test_nested_directory_creation(self) -> None:
        """Test that write creates nested parent directories."""
        nested_path = self.temp_dir / "level1" / "level2" / "data.json"
        store = AsyncJsonFileStore(
            path=nested_path,
            backup_dir=self.backup_dir,
        )

        await store.write({"nested": "directory"})

        self.assertTrue(nested_path.exists())
        self.assertTrue(nested_path.parent.exists())

    async def test_empty_dict_write_and_read(self) -> None:
        """Test writing and reading an empty dictionary."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        await store.write({})

        result = await store.read()
        self.assertEqual(result, {})
        self.assertIsInstance(result, dict)

    async def test_complex_nested_data_structures(self) -> None:
        """Test handling of complex nested data structures."""
        complex_data: JsonDict = {
            "users": [
                {"id": 1, "name": "Alice", "roles": ["admin", "user"]},
                {"id": 2, "name": "Bob", "roles": ["user"]},
            ],
            "metadata": {
                "version": "1.0",
                "config": {"timeout": 30, "retries": 3},
            },
            "flags": {"enabled": True, "debug": False},
        }

        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        await store.write(complex_data)
        result = await store.read()

        self.assertEqual(result, complex_data)
        users = _as_json_array(result["users"])
        first_user = _as_json_object(users[0])
        metadata = _as_json_object(result["metadata"])
        config = _as_json_object(metadata["config"])
        flags = _as_json_object(result["flags"])
        self.assertEqual(len(users), 2)
        self.assertEqual(first_user["name"], "Alice")
        self.assertEqual(config["timeout"], 30)
        self.assertTrue(flags["enabled"])

    async def test_update_with_exception_in_updater(self) -> None:
        """Test that exceptions in updater are propagated."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        def failing_updater(_: JsonDict) -> None:
            raise ValueError("Intentional error")

        with self.assertRaises(ValueError) as context:
            await store.update(failing_updater)

        self.assertIn("Intentional error", str(context.exception))

    async def test_lock_is_per_instance(self) -> None:
        """Test that each store instance has its own lock."""
        store1 = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )
        store2 = AsyncJsonFileStore(
            path=self.temp_dir / "other.json",
            backup_dir=self.backup_dir,
        )

        self.assertIsNot(store1._lock, store2._lock)

    async def test_dataclass_slots_optimization(self) -> None:
        """Test that dataclass uses slots for memory optimization."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        # Slots prevent __dict__ from being created
        self.assertFalse(hasattr(store, "__dict__"))

    async def test_multiple_updates_maintain_consistency(self) -> None:
        """Test that multiple sequential updates maintain data consistency."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        # First update
        await store.update(lambda d: d.update({"step": 1, "value": "first"}))

        result = await store.update(
            lambda d: d.update({"step": 2, "value": "second", "additional": True})
        )

        self.assertEqual(result["step"], 2)
        self.assertEqual(result["value"], "second")
        self.assertTrue(result["additional"])

        final = await store.read()
        self.assertEqual(final, result)

    async def test_updater_type_hint_compatibility(self) -> None:
        """Test that both sync and async updaters satisfy type hints."""
        store = AsyncJsonFileStore(
            path=self.test_file,
            backup_dir=self.backup_dir,
        )

        def sync_updater(data: JsonDict) -> None:
            data.update({"sync": True})

        async def async_updater(_: JsonDict) -> None:
            await asyncio.sleep(0)

        await store.update(sync_updater)
        await store.update(async_updater)

    async def test_read_missing_file_returns_empty_dict(self) -> None:
        store = AsyncJsonFileStore(path=self.test_file, backup_dir=self.backup_dir)

        data = await store.read()

        self.assertEqual(data, {})
        self.assertIsInstance(data, dict)

    async def test_write_then_read_roundtrip(self) -> None:
        store = AsyncJsonFileStore(path=self.test_file, backup_dir=self.backup_dir)
        payload: JsonDict = {"a": 1, "b": {"c": "x"}}

        await store.write(payload)
        reloaded = await store.read()

        self.assertEqual(reloaded, payload)
        self.assertTrue(self.test_file.exists())

    async def test_update_sync_updater_persists(self) -> None:
        store = AsyncJsonFileStore(path=self.test_file, backup_dir=self.backup_dir)

        def updater(d: JsonDict) -> None:
            d["count"] = int(d.get("count", 0)) + 1

        result = await store.update(updater)
        again = await store.read()

        self.assertEqual(result, {"count": 1})
        self.assertEqual(again, {"count": 1})

    async def test_update_async_updater_persists(self) -> None:
        store = AsyncJsonFileStore(path=self.test_file, backup_dir=self.backup_dir)

        async def updater(d: JsonDict) -> None:
            await asyncio.sleep(0)
            d["status"] = "ok"

        result = await store.update(updater)
        again = await store.read()

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(again.get("status"), "ok")

    async def test_update_is_serialized_by_lock(self) -> None:
        store = AsyncJsonFileStore(path=self.test_file, backup_dir=self.backup_dir)
        await store.write({"counter": 0})

        async def inc(d: JsonDict) -> None:
            current = int(d.get("counter", 0))
            await asyncio.sleep(0.01)
            d["counter"] = current + 1

        await asyncio.gather(store.update(inc), store.update(inc), store.update(inc))
        final = await store.read()

        self.assertEqual(final.get("counter"), 3)

    async def test_backup_dir_none_uses_default_backup_dir_constant(self) -> None:
        # Ensure we don't touch the real configured BACKUP_DIR by patching the
        # imported constant in utils.json_utils (where it is used).
        with patch("utils.json_utils.BACKUP_DIR", self.backup_dir):
            store = AsyncJsonFileStore(
                path=self.test_file, backup_amount=1, backup_dir=None
            )

            await store.write({"v": 1})
            await store.write({"v": 2})

        backups = list(
            self.backup_dir.glob(f"{self.test_file.stem}_*{self.test_file.suffix}")
        )
        self.assertEqual(len(backups), 1)

        with backups[0].open("r", encoding="utf-8") as f:
            backup_payload = json.load(f)
        self.assertEqual(backup_payload, {"v": 1})


class TestJsonDictTypeAlias(unittest.TestCase):
    """Test cases for JsonDict type alias."""

    def test_json_dict_accepts_valid_types(self) -> None:
        """Test that JsonDict accepts standard JSON-serializable types."""
        valid_dict: JsonDict = {
            "string": "value",
            "int": 42,
            "float": 3.14,
            "bool": True,
            "null": None,
            "list": [1, 2, 3],
            "nested": {"key": "value"},
        }

        self.assertIsInstance(valid_dict, dict)
        self.assertEqual(valid_dict["string"], "value")


class TestUpdaterTypeAlias(unittest.IsolatedAsyncioTestCase):
    """Test cases for Updater type alias."""

    def test_updater_sync_function(self) -> None:
        """Test that Updater accepts synchronous functions."""

        def sync_func(data: JsonDict) -> None:
            data["updated"] = True

        updater: Updater = sync_func
        test_data: JsonDict = {}
        updater(test_data)

        self.assertTrue(test_data["updated"])

    async def test_updater_async_function(self) -> None:
        """Test that Updater accepts asynchronous functions."""

        async def async_func(data: JsonDict) -> None:
            await asyncio.sleep(0.001)
            data["async_updated"] = True

        updater: Updater = async_func
        test_data: JsonDict = {}
        await updater(test_data)

        self.assertTrue(test_data["async_updated"])


if __name__ == "__main__":
    unittest.main()
