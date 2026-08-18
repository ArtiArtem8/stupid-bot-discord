from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import cast, override

import config
from api.birthday_models import BirthdayGuildConfig, BirthdayGuildDict
from repositories.base_repository import BaseRepository
from repositories.json_object_store import JsonObjectStore
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

    def __init__(self, store: JsonObjectStore | None = None) -> None:
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

    async def _update_guild(
        self,
        guild_id: int,
        mutation: Callable[[BirthdayGuildConfig | None], BirthdayGuildConfig | None],
    ) -> BirthdayGuildConfig | None:
        """Apply one guild mutation to the latest stored aggregate."""
        result: BirthdayGuildConfig | None = None
        guild_key = str(guild_id)

        def _updater(data: JsonObject) -> None:
            nonlocal result
            raw = data.get(guild_key)
            current = None if raw is None else _decode_guild_config(guild_id, raw)
            if raw is not None and current is None:
                raise ValueError(f"Invalid birthday config for guild {guild_id}")

            result = mutation(current)
            if result is None:
                data.pop(guild_key, None)
                return
            data[guild_key] = cast(JsonValue, cast(object, result.to_dict()))

        await self._store.update(_updater)
        return result

    async def set_user_birthday(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int,
        user_id: int,
        user_name: str,
        birthday: str,
    ) -> BirthdayGuildConfig:
        """Set one user's birthday without replacing concurrent guild changes."""

        def _mutation(
            current: BirthdayGuildConfig | None,
        ) -> BirthdayGuildConfig:
            guild_config = current or BirthdayGuildConfig(
                guild_id, server_name, channel_id
            )
            user = guild_config.get_or_create_user(user_id, user_name)
            user.birthday = birthday
            return guild_config

        result = await self._update_guild(guild_id, _mutation)
        if result is None:
            raise RuntimeError("Birthday mutation unexpectedly deleted guild config")
        return result

    async def configure_guild(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int,
        birthday_role_id: int | None,
    ) -> BirthdayGuildConfig:
        """Update birthday delivery settings on the latest guild config."""

        def _mutation(
            current: BirthdayGuildConfig | None,
        ) -> BirthdayGuildConfig:
            guild_config = current or BirthdayGuildConfig(
                guild_id, server_name, channel_id
            )
            guild_config.channel_id = channel_id
            guild_config.birthday_role_id = birthday_role_id
            return guild_config

        result = await self._update_guild(guild_id, _mutation)
        if result is None:
            raise RuntimeError("Birthday mutation unexpectedly deleted guild config")
        return result

    async def clear_user_birthday(
        self, guild_id: int, user_id: int
    ) -> tuple[bool, bool]:
        """Clear one birthday and return (guild exists, birthday was present)."""
        guild_exists = False
        cleared = False

        def _mutation(
            current: BirthdayGuildConfig | None,
        ) -> BirthdayGuildConfig | None:
            nonlocal guild_exists, cleared
            if current is None:
                return None
            guild_exists = True
            user = current.get_user(user_id)
            if user is None or not user.has_birthday():
                return current
            user.clear_birthday()
            cleared = True
            return current

        await self._update_guild(guild_id, _mutation)
        return guild_exists, cleared

    async def record_congratulation(
        self, guild_id: int, user_id: int, congratulation_date: date
    ) -> bool:
        """Record a sent congratulation without replacing concurrent changes."""
        recorded = False

        def _mutation(
            current: BirthdayGuildConfig | None,
        ) -> BirthdayGuildConfig | None:
            nonlocal recorded
            if current is None:
                return None
            user = current.get_user(user_id)
            if user is None or user.was_congratulated_today(congratulation_date):
                return current
            user.add_congratulation(congratulation_date)
            recorded = True
            return current

        await self._update_guild(guild_id, _mutation)
        return recorded

    async def get_all_guild_ids(self) -> list[int]:
        """Get list of all guild IDs in the store."""
        data = await self._store.read()
        return [int(k) for k in data if k.isdigit()]
