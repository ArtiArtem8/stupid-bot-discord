from __future__ import annotations

import logging
from typing import Never, TypeGuard, cast, overload, override

import config
from api.blocking_models import (
    BlockedUser,
    BlockedUserDict,
    BlockHistoryEntryDict,
    GuildData,
    NameHistoryEntryDict,
)
from repositories.base_repository import BaseRepository
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject, JsonValue

type BlockedUserKey = tuple[int, int]  # (guild_id, user_id)

logger = logging.getLogger(__name__)


def _is_block_history_entry_dict(value: object) -> TypeGuard[BlockHistoryEntryDict]:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("admin_id"), str)
        and isinstance(value.get("reason"), str)
        and isinstance(value.get("timestamp"), str)
    )


def _is_name_history_entry_dict(value: object) -> TypeGuard[NameHistoryEntryDict]:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("username"), str) and isinstance(
        value.get("timestamp"), str
    )


def _is_blocked_user_dict(value: object) -> TypeGuard[BlockedUserDict]:
    if not isinstance(value, dict):
        return False

    if not isinstance(value.get("user_id"), str):
        return False
    if not isinstance(value.get("current_username"), str):
        return False

    cgn = value.get("current_global_name")
    if cgn is not None and not isinstance(cgn, str):
        return False

    if not isinstance(value.get("blocked"), bool):
        return False

    bh = value.get("block_history")
    uh = value.get("unblock_history")
    nh = value.get("name_history")

    if not isinstance(bh, list) or not all(
        _is_block_history_entry_dict(cast(object, x)) for x in bh
    ):
        return False
    if not isinstance(uh, list) or not all(
        _is_block_history_entry_dict(cast(object, x)) for x in uh
    ):
        return False
    if not isinstance(nh, list) or not all(
        _is_name_history_entry_dict(cast(object, x)) for x in nh
    ):
        return False

    return True


def _try_decode_user(value: object) -> BlockedUser | None:
    if not _is_blocked_user_dict(value):
        return None
    return BlockedUser.from_dict(value)


class BlockingRepository(BaseRepository[BlockedUser, BlockedUserKey]):
    def __init__(self, store: AsyncJsonFileStore | None = None) -> None:
        self._store = store or AsyncJsonFileStore(config.BLOCKED_USERS_FILE)

    def _get_users_map_raw(
        self, data: JsonObject, guild_id: int
    ) -> dict[str, JsonValue] | None:
        """Safely extract the users map for a guild from the JSON data."""
        raw_guild = data.get(str(guild_id))
        if not isinstance(raw_guild, dict):
            return {}

        raw_users = raw_guild.get("users")
        if not isinstance(raw_users, dict):
            return {}

        return raw_users

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

    def _ensure_users_map_raw(
        self, data: JsonObject, guild_id: int
    ) -> dict[str, JsonValue]:
        guild_key = str(guild_id)

        raw_guild = data.get(guild_key)
        if not isinstance(raw_guild, dict):
            raw_guild = cast(JsonObject, {"users": {}})
            data[guild_key] = raw_guild

        raw_users = raw_guild.get("users")
        if not isinstance(raw_users, dict):
            raw_users = {}
            raw_guild["users"] = raw_users

        return raw_users

    @override
    async def get(self, key: BlockedUserKey) -> BlockedUser | None:
        """Get a single user by (guild_id, user_id)."""
        guild_id, user_id = key
        data = await self._store.read()

        users_map = self._get_users_map_raw(data, guild_id)
        if users_map is None:
            return None
        raw_user = users_map.get(str(user_id))

        return _try_decode_user(raw_user)

    @override
    async def get_all(self) -> list[BlockedUser]:
        """Get all users from all guilds."""
        data = await self._store.read()
        all_users: list[BlockedUser] = []

        for guild_id, guild_raw in data.items():
            if not isinstance(guild_raw, dict):
                continue

            users_raw = guild_raw.get("users")
            if not isinstance(users_raw, dict):
                continue

            for user_raw in users_raw.values():
                user = _try_decode_user(user_raw)
                if user is not None:
                    all_users.append(user)
                else:
                    logger.warning(
                        "Skipping invalid blocked-user record in guild %s", guild_id
                    )

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
            users_map = self._ensure_users_map_raw(data, guild_id)
            users_map[str(user_id)] = cast(JsonValue, cast(object, entity.to_dict()))

        await self._store.update(_updater)

    @override
    async def delete(self, key: BlockedUserKey) -> None:
        """Delete a user by (guild_id, user_id)."""
        guild_id, user_id = key

        def _updater(data: JsonObject) -> None:
            users_map = self._get_users_map_raw(data, guild_id)
            if users_map is None:
                return
            users_map.pop(str(user_id), None)

        await self._store.update(_updater)

    async def get_all_for_guild(self, guild_id: int) -> list[BlockedUser]:
        """Get all users for a single guild."""
        data = await self._store.read()
        users_map = self._get_users_map_raw(data, guild_id)
        if users_map is None:
            return []

        return [
            user
            for u in users_map.values()
            if (user := _try_decode_user(u)) is not None
        ]
