from __future__ import annotations

from dataclasses import dataclass
from typing import override

import config
from repositories.base_repository import BaseRepository
from utils import AsyncJsonFileStore


@dataclass(frozen=True, slots=True)
class VolumeData:
    guild_id: int
    volume: int


class VolumeRepository(BaseRepository[VolumeData, int]):
    def __init__(self, store: AsyncJsonFileStore | None = None) -> None:
        self._store = store or AsyncJsonFileStore(config.MUSIC_VOLUME_FILE)

    @override
    async def get(self, key: int) -> VolumeData | None:
        data = await self._store.read()
        raw = data.get(str(key))
        if raw is None:
            return None
        return VolumeData(guild_id=key, volume=int(raw))

    @override
    async def get_all(self) -> list[VolumeData]:
        data = await self._store.read()
        return [
            VolumeData(guild_id=int(gid), volume=int(vol)) for gid, vol in data.items()
        ]

    @override
    async def save(self, entity: VolumeData, key: int | None = None) -> None:
        def _upd(d: dict[str, object]) -> None:
            d[str(entity.guild_id)] = entity.volume

        await self._store.update(_upd)

    @override
    async def delete(self, key: int) -> None:
        def _upd(d: dict[str, object]) -> None:
            d.pop(str(key), None)

        await self._store.update(_upd)

    async def get_volume(self, guild_id: int) -> int:
        entity = await self.get(guild_id)
        return entity.volume if entity else config.MUSIC_DEFAULT_VOLUME
