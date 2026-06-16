from __future__ import annotations

import copy
import inspect

from repositories.json_object_store import JsonUpdater
from utils.json_types import JsonObject


class InMemoryJsonStore:
    """In-memory async JSON store to avoid filesystem in repository tests."""

    def __init__(self, initial_data: JsonObject | None = None) -> None:
        data: JsonObject = {} if initial_data is None else copy.deepcopy(initial_data)
        self._data = data
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
