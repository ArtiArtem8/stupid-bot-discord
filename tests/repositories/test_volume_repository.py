"""Tests for volume repository storage behavior.
Covers default fallback, CRUD paths, and preservation of other entries.
"""

from __future__ import annotations

import copy
import unittest
from collections.abc import Callable
from typing import override
from unittest.mock import patch

import config
from repositories.volume_repository import VolumeData, VolumeRepository
from utils.json_types import JsonObject


class FakeAsyncJsonFileStore:
    """In-memory async store for testing."""

    def __init__(self, initial_data: JsonObject | None = None) -> None:
        self._data = copy.deepcopy(initial_data or {})

    async def read(self) -> JsonObject:
        return copy.deepcopy(self._data)

    async def update(self, updater: Callable[[JsonObject], None]) -> None:
        data = copy.deepcopy(self._data)
        updater(data)
        self._data = data

    @property
    def data(self) -> JsonObject:
        return copy.deepcopy(self._data)


class TestVolumeRepository(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self._p_default = patch.object(config, "MUSIC_DEFAULT_VOLUME", 100)
        self._p_file = patch.object(config, "MUSIC_VOLUME_FILE", "mock_volume.json")
        self._p_default.start()
        self._p_file.start()
        self.store = FakeAsyncJsonFileStore()
        self.repo = VolumeRepository(store=self.store)

    @override
    def tearDown(self) -> None:
        self._p_default.stop()
        self._p_file.stop()

    async def test_get_volume_existing(self) -> None:
        self.store = FakeAsyncJsonFileStore({"123": 50})
        self.repo = VolumeRepository(store=self.store)

        vol = await self.repo.get_volume(123)

        self.assertEqual(vol, 50)

    async def test_get_volume_default(self) -> None:
        self.store = FakeAsyncJsonFileStore({})
        self.repo = VolumeRepository(store=self.store)

        vol = await self.repo.get_volume(999)

        self.assertEqual(vol, 100)  # Should return config.MUSIC_DEFAULT_VOLUME

    async def test_save_updates_one_user_preserves_others(self) -> None:
        initial_data = {"123": 50, "999": 100}
        self.store = FakeAsyncJsonFileStore(initial_data)
        self.repo = VolumeRepository(store=self.store)

        await self.repo.save(VolumeData(123, 75))

        final_data = self.store.data
        self.assertEqual(final_data["123"], 75)
        self.assertEqual(final_data["999"], 100)

    async def test_save_creates_new_entry_if_missing(self) -> None:
        self.store = FakeAsyncJsonFileStore({})
        self.repo = VolumeRepository(store=self.store)

        await self.repo.save(VolumeData(123, 50))

        final_data = self.store.data
        self.assertEqual(final_data["123"], 50)

    async def test_save_new_volume(self) -> None:
        self.store = FakeAsyncJsonFileStore({})
        self.repo = VolumeRepository(store=self.store)

        await self.repo.save(VolumeData(456, 80))

        final_data = self.store.data
        self.assertEqual(final_data["456"], 80)

    async def test_get_entity_returns_none_if_missing(self) -> None:
        self.repo = VolumeRepository(store=FakeAsyncJsonFileStore({}))

        entity = await self.repo.get(123)

        self.assertIsNone(entity)

    async def test_get_entity_returns_data_object(self) -> None:
        self.store = FakeAsyncJsonFileStore({"123": 42})
        self.repo = VolumeRepository(store=self.store)

        entity = await self.repo.get(123)

        self.assertIsNotNone(entity)
        assert entity is not None
        self.assertEqual(entity.guild_id, 123)
        self.assertEqual(entity.volume, 42)

    async def test_delete_removes_entry(self) -> None:
        self.store = FakeAsyncJsonFileStore({"123": 50, "456": 80})
        self.repo = VolumeRepository(store=self.store)

        await self.repo.delete(123)

        final_data = self.store.data
        self.assertNotIn("123", final_data)
        self.assertIn("456", final_data)
