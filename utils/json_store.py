import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path

from config import ENCODING
from utils.json_types import JsonEncodableObject, JsonObject, freeze_json_object
from utils.json_utils import get_json, save_json

logger = logging.getLogger(__name__)

type JsonDict = JsonObject
type Updater = Callable[[JsonObject], Awaitable[None] | None]


@dataclass(slots=True)
class AsyncJsonFileStore:
    """Async access to one mutable JSON object file.

    Each instance owns its synchronization lock. A process must therefore share
    one authoritative store instance for a path; concurrent instances for the
    same file are not serialized. Physical file operations run in worker threads.
    Cancellation during a write waits for its worker before releasing the lock.
    """

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

    async def read(self) -> JsonObject:
        """Read the current object, returning an empty object for a missing file."""
        data = await asyncio.to_thread(get_json, self.path, encoding=self.encoding)
        return {} if data is None else data

    async def write(self, data: JsonEncodableObject) -> None:
        """Replace the stored object while serializing writes through this instance."""
        async with self._lock:
            await self._write_unlocked(data)

    async def _write_unlocked(self, data: JsonEncodableObject) -> None:
        work = asyncio.create_task(
            asyncio.to_thread(
                save_json,
                self.path,
                data,
                self.backup_amount,
                backup_dir=self.backup_dir,
                encoding=self.encoding,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        # A cancelled coroutine cannot cancel a running thread. Retain file
        # ownership even if shutdown cancels the caller more than once.
        while True:
            try:
                await asyncio.shield(work)
                break
            except asyncio.CancelledError as error:
                cancellation = error
            except Exception:
                if cancellation is None:
                    raise
                logger.exception("JSON write failed while its caller was cancelled")
                break
        if cancellation is not None:
            raise cancellation

    async def update(self, updater: Updater) -> JsonObject:
        """Apply one atomic mutation to the stored JSON object.

        Concurrent updates through this store instance are serialized. The
        updater may mutate synchronously or asynchronously. A no-op mutation
        returns the current object without rewriting the file or rotating backups.
        """
        async with self._lock:
            data = await self.read()
            original = freeze_json_object(data)
            result = updater(data)
            if inspect.isawaitable(result):
                await result
            if data != original:
                await self._write_unlocked(data)
            return data
