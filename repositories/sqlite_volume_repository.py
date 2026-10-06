"""Persist guild volume settings through the application's shared database."""

from collections.abc import Sequence
from typing import override

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

import config
from repositories.base_repository import BaseRepository
from repositories.sqlite.database import Database
from repositories.sqlite.schema import volumes
from repositories.volume_repository import VolumeData


class SQLiteVolumeRepository(BaseRepository[VolumeData, int]):
    """Own volume queries; the shared Database owns transactions and shutdown.

    Guilds need no birthday registration. Missing settings use the existing
    configured default. Storage does not clamp values or change playback policy.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def get(self, key: int) -> VolumeData | None:
        async with self._database.transaction() as connection:
            volume = await connection.scalar(
                select(volumes.c.volume).where(volumes.c.guild_id == key)
            )
        return None if volume is None else VolumeData(key, volume)

    @override
    async def get_all(self) -> list[VolumeData]:
        async with self._database.transaction() as connection:
            rows = await connection.execute(
                select(volumes.c.guild_id, volumes.c.volume).order_by(
                    volumes.c.guild_id
                )
            )
            return [VolumeData(guild_id, volume) for guild_id, volume in rows]

    @override
    async def save(self, entity: VolumeData, key: int | None = None) -> None:
        # The existing JSON contract uses entity.guild_id, ignoring the optional key.
        async with self._database.transaction() as connection:
            statement = insert(volumes).values(
                guild_id=entity.guild_id, volume=entity.volume
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[volumes.c.guild_id],
                    set_={"volume": statement.excluded.volume},
                    where=volumes.c.volume != statement.excluded.volume,
                )
            )

    @override
    async def delete(self, key: int) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(volumes.delete().where(volumes.c.guild_id == key))

    async def get_volume(self, guild_id: int) -> int:
        """Return saved volume, including zero, or the configured default."""
        entity = await self.get(guild_id)
        return config.MUSIC_DEFAULT_VOLUME if entity is None else entity.volume

    async def import_volumes(self, entries: Sequence[VolumeData]) -> int:
        """Insert missing settings atomically; reject any differing saved value.

        Identical retries are no-ops. A conflict rolls back all inserts in the
        attempt. The operator must resolve differences explicitly before retrying.
        """
        inserted = 0
        async with self._database.transaction() as connection:
            for entry in entries:
                existing = await connection.scalar(
                    select(volumes.c.volume).where(volumes.c.guild_id == entry.guild_id)
                )
                if existing is not None:
                    if existing != entry.volume:
                        raise ValueError(
                            f"Conflicting volume for guild {entry.guild_id}"
                        )
                    continue
                await connection.execute(
                    insert(volumes).values(guild_id=entry.guild_id, volume=entry.volume)
                )
                inserted += 1
        return inserted
