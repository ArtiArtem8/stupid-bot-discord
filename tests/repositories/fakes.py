from __future__ import annotations

import copy
import inspect
from typing import cast

from repositories.json_object_store import JsonUpdater
from utils.json_types import JsonObject


class InMemoryJsonStore:
    """In-memory async JSON store to avoid filesystem in repository tests."""

    def __init__(self, initial_data: object | None = None) -> None:
        self._data = json_object(initial_data or {})
        self.update_calls = 0

    async def read(self) -> JsonObject:
        return copy.deepcopy(self._data)

    async def update(self, updater: JsonUpdater) -> JsonObject:
        self.update_calls += 1
        data = copy.deepcopy(self._data)
        result = updater(data)
        if inspect.isawaitable(result):
            await result
        self._data = data
        return data

    @property
    def data(self) -> JsonObject:
        return copy.deepcopy(self._data)


def json_object(value: object) -> JsonObject:
    """Convert loose test fixture data into the JSON object contract."""
    return cast(JsonObject, copy.deepcopy(value))
