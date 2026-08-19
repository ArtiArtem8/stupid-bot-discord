from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from utils.json_types import JsonObject

type JsonUpdater = Callable[[JsonObject], Awaitable[None] | None]


class JsonObjectStore(Protocol):
    """JSON object operations required by repositories.

    Implementations own serialization for each mutable resource. Repository
    owners must retain one implementation instance for the resource lifetime.
    """

    async def read(self) -> JsonObject: ...

    async def update(self, updater: JsonUpdater) -> JsonObject: ...
