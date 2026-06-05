from __future__ import annotations

import logging
from typing import cast, override

import config
from api.blocking_models import BlockedUser
from repositories.base_repository import BaseRepository
from repositories.blocking_codec import as_json_object, try_decode_user
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject, JsonValue

type BlockedUserKey = tuple[int, int]  # (guild_id, user_id)

logger = logging.getLogger(__name__)


class BlockingRepository(BaseRepository[BlockedUser, BlockedUserKey]):
    def __init__(self, store: AsyncJsonFileStore | None = None) -> None:
        self._store = store or AsyncJsonFileStore(config.BLOCKED_USERS_FILE)

    def _get_users_map_raw(self, data: JsonObject, guild_id: int) -> JsonObject:
        """Safely extract the users map for a guild from the JSON data."""
        raw_guild = as_json_object(data.get(str(guild_id)))
        if raw_guild is None:
            return {}

        raw_users = as_json_object(raw_guild.get("users"))
        if raw_users is None:
            return {}

        return raw_users

    def _ensure_users_map_raw(self, data: JsonObject, guild_id: int) -> JsonObject:
        """Ensure that the guild/users object exists and return the users map."""
        guild_key = str(guild_id)

        raw_guild = as_json_object(data.get(guild_key))
        if raw_guild is None:
            raw_guild = {}
            data[guild_key] = raw_guild

        raw_users = as_json_object(raw_guild.get("users"))
        if raw_users is None:
            raw_users = {}
            raw_guild["users"] = raw_users

        return raw_users

    @override
    async def get(self, key: BlockedUserKey) -> BlockedUser | None:
        """Get a single user by (guild_id, user_id)."""
        guild_id, user_id = key
        data = await self._store.read()

        users_map = self._get_users_map_raw(data, guild_id)
        raw_user = users_map.get(str(user_id))

        return try_decode_user(raw_user)

    @override
    async def get_all(self) -> list[BlockedUser]:
        """Get all users from all guilds."""
        data = await self._store.read()
        all_users: list[BlockedUser] = []

        for guild_id, guild_value in data.items():
            guild_data = as_json_object(guild_value)
            if guild_data is None:
                continue

            users_data = as_json_object(guild_data.get("users"))
            if users_data is None:
                continue

            for user_value in users_data.values():
                user = try_decode_user(user_value)
                if user is not None:
                    all_users.append(user)
                else:
                    logger.warning(
                        "Skipping invalid blocked-user record in guild %s", guild_id
                    )

        return all_users

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
            users_map = self._ensure_users_map_raw(data, guild_id)
            users_map[str(user_id)] = cast(JsonValue, cast(object, entity.to_dict()))

        await self._store.update(_updater)

    @override
    async def delete(self, key: BlockedUserKey) -> None:
        """Delete a user by (guild_id, user_id)."""
        guild_id, user_id = key

        def _updater(data: JsonObject) -> None:
            users_map = self._get_users_map_raw(data, guild_id)
            users_map.pop(str(user_id), None)

        await self._store.update(_updater)

    async def get_all_for_guild(self, guild_id: int) -> list[BlockedUser]:
        """Get all users for a single guild."""
        data = await self._store.read()
        users_map = self._get_users_map_raw(data, guild_id)

        return [
            user for u in users_map.values() if (user := try_decode_user(u)) is not None
        ]
