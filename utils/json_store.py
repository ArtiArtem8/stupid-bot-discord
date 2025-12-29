import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path

from config import ENCODING
from utils.json_utils import get_json, save_json

type JsonDict = dict[str, object]
type Updater = Callable[[JsonDict], None | Awaitable[None]]


@dataclass(slots=True)
class AsyncJsonFileStore:
    path: str | PathLike[str]
    backup_amount: int = 3
    backup_dir: Path | None = None
    encoding: str = ENCODING
    _lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )

    async def read(self) -> JsonDict:
        data = await asyncio.to_thread(get_json, self.path, encoding=self.encoding)
        return data or {}

    async def write(self, data: Mapping[str, object]) -> None:
        await asyncio.to_thread(
            save_json,
            self.path,
            data,
            self.backup_amount,
            backup_dir=self.backup_dir,
            encoding=self.encoding,
        )

    async def update(self, updater: Updater) -> JsonDict:
        """Lock + read + mutate + write, returning the final data."""
        async with self._lock:
            data = await self.read()
            result = updater(data)
            if inspect.isawaitable(result):
                await result
            await self.write(data)
            return data
