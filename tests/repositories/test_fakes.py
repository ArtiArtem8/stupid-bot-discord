"""Tests for repository test doubles."""

from __future__ import annotations

import unittest

from tests.repositories.fakes import InMemoryJsonStore
from utils.json_types import JsonObject


class TestInMemoryJsonStore(unittest.IsolatedAsyncioTestCase):
    async def test_update_result_does_not_alias_stored_data(self) -> None:
        store = InMemoryJsonStore()

        def updater(data: JsonObject) -> None:
            data["value"] = 1

        result = await store.update(updater)
        result["value"] = 2

        stored = await store.read()

        self.assertEqual(stored["value"], 1)
