"""Serialize persisted volume intent with its corresponding remote application."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from repositories.volume_repository import VolumeData, VolumeStore


class VolumeSettings:
    """Own per-guild volume operations shared by commands, joins and recovery.

    Without this owner, command B can finish its HTTP update before command A,
    or a healer can apply an old snapshot after a new command. The operation
    holds no SQL connection while its caller applies the returned current value.
    """

    def __init__(self, repository: VolumeStore) -> None:
        self.repository = repository
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    @asynccontextmanager
    async def operation(
        self, guild_id: int, *, desired: int | None = None
    ) -> AsyncGenerator[int]:
        """Commit optional intent and retain ordering through remote application."""
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        async with lock:
            if desired is not None:
                await self.repository.save(VolumeData(guild_id, desired))
            yield await self.repository.get_volume(guild_id)
