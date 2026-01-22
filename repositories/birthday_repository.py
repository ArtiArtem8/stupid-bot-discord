from __future__ import annotations

import logging
from typing import cast, override

import config
from api.birthday_models import BirthdayGuildConfig, BirthdayGuildDict
from repositories.base_repository import BaseRepository
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)


def _decode_guild_config(guild_id: int, raw: object) -> BirthdayGuildConfig | None:
    if not isinstance(raw, dict):
        return None
    try:
        return BirthdayGuildConfig.from_dict(
            guild_id, cast(BirthdayGuildDict, cast(object, raw))
        )
    except (KeyError, ValueError, TypeError):
        return None


class BirthdayRepository(BaseRepository[BirthdayGuildConfig, int]):
    """Repository for managing birthday data asynchronously."""

    def __init__(self, store: AsyncJsonFileStore | None = None) -> None:
        self._store = store or AsyncJsonFileStore(config.BIRTHDAY_FILE)

    @override
    async def get(self, key: int) -> BirthdayGuildConfig | None:
        """Get guild config by guild_id."""
        data = await self._store.read()
        guild_key = str(key)

        if guild_key not in data:
            return None

        guild_data = data.get(str(key))
        return _decode_guild_config(key, guild_data)

    @override
    async def get_all(self) -> list[BirthdayGuildConfig]:
        """Get all guild configs."""
        data = await self._store.read()
        results: list[BirthdayGuildConfig] = []

        for guild_key, guild_data in data.items():
            if not guild_key.isdigit():
                continue
            cfg = _decode_guild_config(int(guild_key), guild_data)
            if cfg is None:
                logger.warning(
                    "Skipping invalid birthday config for guild %s", guild_key
                )
                continue
            results.append(cfg)

        return results

    @override
    async def save(self, entity: BirthdayGuildConfig, key: int | None = None) -> None:
        """Save a guild config."""
        guild_id = key if key is not None else entity.guild_id

        def _updater(data: JsonObject) -> None:
            data[str(guild_id)] = cast(JsonValue, cast(object, entity.to_dict()))

        await self._store.update(_updater)

    @override
    async def delete(self, key: int) -> None:
        """Delete a guild config by guild_id."""

        def _updater(data: JsonObject) -> None:
            data.pop(str(key), None)

        await self._store.update(_updater)

    async def get_all_guild_ids(self) -> list[int]:
        """Get list of all guild IDs in the store."""
        data = await self._store.read()
        return [int(k) for k in data.keys() if k.isdigit()]
