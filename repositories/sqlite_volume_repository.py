"""Persist guild volume intent through the application's shared database."""

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

import config
from repositories.sqlite.database import Database
from repositories.sqlite.identity import discord_id, ensure_guild
from repositories.sqlite.schema import music_settings
from repositories.volume_repository import VolumeData


class SQLiteVolumeRepository:
    """Own bounded settings queries, without remote playback side effects."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, key: int) -> VolumeData | None:
        discord_id(key)
        async with self._database.transaction() as connection:
            volume = await connection.scalar(
                select(music_settings.c.volume).where(music_settings.c.guild_id == key)
            )
        return None if volume is None else VolumeData(key, volume)

    async def get_all(self) -> list[VolumeData]:
        async with self._database.transaction() as connection:
            rows = await connection.execute(
                select(music_settings.c.guild_id, music_settings.c.volume).order_by(
                    music_settings.c.guild_id
                )
            )
            return [VolumeData(guild_id, volume) for guild_id, volume in rows]

    async def save(self, entity: VolumeData) -> None:
        """Commit desired volume; unchanged intent is a no-op."""
        _validate_volume(entity.volume)
        async with self._database.transaction() as connection:
            await ensure_guild(connection, entity.guild_id)
            statement = insert(music_settings).values(
                guild_id=entity.guild_id, volume=entity.volume, version=1
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[music_settings.c.guild_id],
                    set_={
                        "volume": entity.volume,
                        "version": music_settings.c.version + 1,
                    },
                    where=music_settings.c.volume != entity.volume,
                )
            )

    async def get_volume(self, guild_id: int) -> int:
        """Return saved volume, including zero, or the configured default."""
        entity = await self.get(guild_id)
        return config.MUSIC_DEFAULT_VOLUME if entity is None else entity.volume


def _validate_volume(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 200:
        raise ValueError("Volume must be an integer between 0 and 200")
