from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from os import PathLike
from typing import Any

from utils.json_utils import get_json, save_json

type JsonDict = dict[str, Any]


@dataclass(slots=True)
class AsyncJsonFileStore:
    path: str | PathLike[str]
    backup_amount: int = 3
    _lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )

    async def read(self) -> JsonDict:
        data = await asyncio.to_thread(get_json, self.path)
        return data or {}

    async def write(self, data: Mapping[str, Any]) -> None:
        await asyncio.to_thread(save_json, self.path, data, self.backup_amount)

    async def update(self, updater: Callable[[JsonDict], None]) -> JsonDict:
        """Lock + read + mutate + write, returning the final data."""
        async with self._lock:
            data = await self.read()
            updater(data)
            await self.write(data)
            return data
