# repositories/blocking_repository.py
from __future__ import annotations

from typing import TYPE_CHECKING, Never, cast, overload, override

import config
from api.blocking_models import BlockedUser, GuildData
from repositories.base_repository import BaseRepository
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject

if TYPE_CHECKING:
    from api.blocking_models import BlockedUserDict

type BlockedUserKey = tuple[int, int]  # (guild_id, user_id)


class BlockingRepository(BaseRepository[BlockedUser, BlockedUserKey]):
    def __init__(self, store: AsyncJsonFileStore | None = None) -> None:
        self._store = store or AsyncJsonFileStore(config.BLOCKED_USERS_FILE)

    def _get_users_map(
        self, data: JsonObject, guild_id: int
    ) -> dict[str, BlockedUserDict]:
        """Safely extract the users map for a guild from the JSON data."""
        guild_key = str(guild_id)
        if guild_key not in data:
            return {}

        guild_data_raw = data[guild_key]
        if not isinstance(guild_data_raw, dict):
            return {}

        # Cast to GuildData first to help type checker
        guild_data = cast(GuildData, cast(object, guild_data_raw))

        users_map = guild_data.get("users")
        if not isinstance(users_map, dict):
            return {}

        return users_map

    def _ensure_guild_data(
        self, data: JsonObject, guild_id: int
    ) -> dict[str, BlockedUserDict]:
        """Ensure guild data structure exists and return the users map."""
        guild_key = str(guild_id)

        # 1. Get or create guild dict
        raw_guild_data = data.get(guild_key)
        if not isinstance(raw_guild_data, dict):
            raw_guild_data = {"users": {}}
            data[guild_key] = cast(JsonObject, cast(object, raw_guild_data))

        guild_data = cast(GuildData, cast(object, raw_guild_data))

        if "users" not in guild_data:
            guild_data["users"] = {}

        return guild_data["users"]

    @override
    async def get(self, key: BlockedUserKey) -> BlockedUser | None:
        """Get a single user by (guild_id, user_id)."""
        guild_id, user_id = key
        data = await self._store.read()

        users_map = self._get_users_map(data, guild_id)
        raw_user = users_map.get(str(user_id))

        if raw_user:
            return BlockedUser.from_dict(raw_user)
        return None

    @override
    async def get_all(self) -> list[BlockedUser]:
        """Get all users from all guilds."""
        data = await self._store.read()
        all_users: list[BlockedUser] = []

        for guild_data_raw in data.values():
            if not isinstance(guild_data_raw, dict):
                continue

            guild_data = cast(GuildData, cast(object, guild_data_raw))

            users_map = guild_data.get("users")
            if not isinstance(users_map, dict):
                continue

            for user_dict in users_map.values():
                all_users.append(BlockedUser.from_dict(user_dict))

        return all_users

    @overload
    async def save(self, entity: BlockedUser) -> Never: ...
    @overload
    async def save(self, entity: BlockedUser, key: BlockedUserKey) -> None: ...
    @override
    async def save(
        self, entity: BlockedUser, key: BlockedUserKey | None = None
    ) -> None:
        """Save a user entity under its (guild_id, user_id) key."""
        if key is None:
            raise ValueError(
                "Key (guild_id, user_id) is required for BlockingRepository.save"
            )

        guild_id, user_id = key

        def _updater(data: JsonObject) -> None:
            users_map = self._ensure_guild_data(data, guild_id)
            users_map[str(user_id)] = entity.to_dict()

        await self._store.update(_updater)

    @override
    async def delete(self, key: BlockedUserKey) -> None:
        """Delete a user by (guild_id, user_id)."""
        guild_id, user_id = key

        def _updater(data: JsonObject) -> None:
            guild_key = str(guild_id)
            if guild_key not in data:
                return

            guild_data = cast(GuildData, cast(object, data[guild_key]))
            users_map = guild_data["users"]
            users_map.pop(str(user_id), None)

        await self._store.update(_updater)

    async def get_all_for_guild(self, guild_id: int) -> list[BlockedUser]:
        """Get all users for a single guild."""
        data = await self._store.read()
        users_map = self._get_users_map(data, guild_id)

        return [BlockedUser.from_dict(u) for u in users_map.values()]
