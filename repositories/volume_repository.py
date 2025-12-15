from __future__ import annotations

import asyncio
from dataclasses import dataclass

import config
from repositories.base_repository import BaseRepository
from utils.json_utils import get_json, save_json


@dataclass
class VolumeData:
    """Entity representing volume configuration for a guild."""

    guild_id: int
    volume: int


class VolumeRepository(BaseRepository[VolumeData]):
    """Repository for managing guild volume settings.
    Persists data to a JSON file specified in config.
    """

    def __init__(self) -> None:
        self.file_path = config.MUSIC_VOLUME_FILE
        self._lock = asyncio.Lock()

    async def get(self, id: str) -> VolumeData | None:
        """Get volume data for a guild ID.
        Returns a VolumeData object with default volume if not found?
        BaseRepository spec says return T | None.
        But for volume we usually want a default.
        Let's follow the data retrieval pattern: if not in DB, return None.
        Service layer handles default.
        """
        data = get_json(self.file_path) or {}
        vol = data.get(id)
        if vol is None:
            return None
        return VolumeData(guild_id=int(id), volume=vol)

    async def get_all(self) -> list[VolumeData]:
        data = get_json(self.file_path) or {}
        return [VolumeData(guild_id=int(gid), volume=vol) for gid, vol in data.items()]

    async def save(self, entity: VolumeData) -> None:
        async with self._lock:
            data = get_json(self.file_path) or {}
            data[str(entity.guild_id)] = entity.volume
            save_json(self.file_path, data)

    async def delete(self, id: str) -> None:
        async with self._lock:
            data = get_json(self.file_path) or {}
            if id in data:
                del data[id]
                save_json(self.file_path, data)

    # Helper for cleaner service usage
    async def get_volume(self, guild_id: int) -> int:
        """Helper to get volume with default fallback."""
        entity = await self.get(str(guild_id))
        return entity.volume if entity else config.MUSIC_DEFAULT_VOLUME
