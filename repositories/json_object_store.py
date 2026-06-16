from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from utils.json_types import JsonObject

type JsonUpdater = Callable[[JsonObject], None | Awaitable[None]]


class JsonObjectStore(Protocol):
    """Current JSON object-store operations used by repositories."""

    async def read(self) -> JsonObject: ...

    async def update(self, updater: JsonUpdater) -> JsonObject: ...
