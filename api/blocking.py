from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.utils import utcnow

import config
from api.blocking_models import (
    BlockedUser,
    NameHistoryEntry,
)
from repositories.blocking_repository import BlockingRepository

if TYPE_CHECKING:
    from api.blocking_models import (
        BlockedUserDict,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "BlockManager",
    "BlockedUser",
    "block_manager",
]


class BlockManager:
    """Manage blocked users across guilds."""

    def __init__(self, repository: BlockingRepository) -> None:
        self.repo = repository

    async def _get_or_create_user(
        self, guild_id: int, member: discord.Member
    ) -> BlockedUser:
        user_id = member.id
        key = (guild_id, user_id)
        user = await self.repo.get(key)
        if user:
            if user.update_name_history(member.display_name, member.name):
                logger.info(
                    "Updated name history for user %d in guild %d. Name: %s",
                    user_id,
                    guild_id,
                    member.display_name,
                )
                await self.repo.save(user, key)
            return user
        new_user = BlockedUser(
            user_id=user_id,
            current_username=member.display_name,
            current_global_name=member.name,
        )
        new_user.name_history.append(
            NameHistoryEntry(
                username=member.display_name,
                timestamp=utcnow(),
            )
        )
        await self.repo.save(new_user, key)  # Save Immediately
        logger.info("Created block entry for %d in %d", user_id, guild_id)
        return new_user

    async def is_user_blocked(self, guild_id: int, user_id: int) -> bool:
        """Check if a user is currently blocked in the guild."""
        user = await self.repo.get((guild_id, user_id))
        return user.is_blocked if user else False

    async def get_guild_users(self, guild_id: int) -> list[BlockedUser]:
        """Get list of all tracked users for a guild."""
        return await self.repo.get_all_for_guild(guild_id)

    async def get_user(self, guild_id: int, user_id: int) -> BlockedUser | None:
        """Get a specific user (Read-only)."""
        return await self.repo.get((guild_id, user_id))

    async def block_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Block a user and save to disk."""
        user = await self._get_or_create_user(guild_id, target)
        if user.is_blocked:
            return user
        user.add_block_entry(admin_id, reason)
        await self.repo.save(user, (guild_id, target.id))
        return user

    async def unblock_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Unblock a user and save to disk."""
        user = await self._get_or_create_user(guild_id, target)
        if not user.is_blocked:
            return user
        user.add_unblock_entry(admin_id, reason)
        await self.repo.save(user, (guild_id, target.id))
        return user


class SyncBlockingRepository:
    def __init__(self) -> None:
        self.file_path = config.BLOCKED_USERS_FILE

    def get_all_grouped(self) -> dict[int, dict[int, BlockedUser]]:
        from utils.json_utils import get_json

        raw_data = get_json(self.file_path) or {}
        result: dict[int, dict[int, BlockedUser]] = {}
        for guild_id_str, guild_data in raw_data.items():
            guild_id = int(guild_id_str)
            users_data = guild_data.get("users", {})
            result[guild_id] = {
                int(uid): BlockedUser.from_dict(u_data)
                for uid, u_data in users_data.items()
            }
        return result

    def save_all_grouped(self, data: dict[int, dict[int, BlockedUser]]) -> None:
        from utils.json_utils import save_json

        output_data: dict[str, dict[str, dict[str, BlockedUserDict]]] = {}
        for guild_id, users_map in data.items():
            output_data[str(guild_id)] = {
                "users": {str(uid): user.to_dict() for uid, user in users_map.items()}
            }
        save_json(self.file_path, output_data)


class BlockManager_:
    """Manage blocked users across guilds."""

    def __init__(self, repository: SyncBlockingRepository) -> None:
        self.repo = repository
        self._cache: dict[int, dict[int, BlockedUser]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy load the entire JSON file into cache."""
        if self._loaded:
            return
        self._cache = self.repo.get_all_grouped()
        self._loaded = True

    def _save_cache(self):
        """Dump the entire cache."""
        self.repo.save_all_grouped(self._cache)

    def _get_or_create_user(self, guild_id: int, member: discord.Member) -> BlockedUser:
        self._ensure_loaded()
        if guild_id not in self._cache:
            self._cache[guild_id] = {}
        guild_cache = self._cache[guild_id]
        user_id = member.id
        if user_id in guild_cache:
            user = guild_cache[user_id]
            if user.update_name_history(member.display_name, member.name):
                logger.info(
                    "Updated name history for user %d in guild %d. Name: %s",
                    user_id,
                    guild_id,
                    member.display_name,
                )
            return user

        new_user = BlockedUser(
            user_id=user_id,
            current_username=member.display_name,
            current_global_name=member.name,
        )
        new_user.name_history.append(
            NameHistoryEntry(
                username=member.display_name,
                timestamp=utcnow(),
            )
        )
        guild_cache[user_id] = new_user
        logger.info("Created block entry for %d in %d", user_id, guild_id)
        return new_user

    def is_user_blocked(self, guild_id: int, user_id: int) -> bool:
        """Check if a user is currently blocked in the guild."""
        self._ensure_loaded()
        user = self._cache.get(guild_id, {}).get(user_id)
        return user.is_blocked if user else False

    def get_guild_users(self, guild_id: int) -> list[BlockedUser]:
        """Get list of all tracked users for a guild."""
        self._ensure_loaded()
        return list(self._cache.get(guild_id, {}).values())

    def get_user(self, guild_id: int, user_id: int) -> BlockedUser | None:
        """Get a specific user (Read-only)."""
        self._ensure_loaded()
        return self._cache.get(guild_id, {}).get(user_id)

    def block_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Block a user and save to disk."""
        user = self._get_or_create_user(guild_id, target)

        if user.is_blocked:
            return user

        user.add_block_entry(admin_id, reason)

        self._save_cache()
        return user

    def unblock_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Unblock a user and save to disk."""
        user = self._get_or_create_user(guild_id, target)

        if not user.is_blocked:
            return user

        user.add_unblock_entry(admin_id, reason)

        self._save_cache()
        return user

    def reload(self):
        """Force reload the entire cache."""
        self._loaded = False
        self._cache.clear()
        self._ensure_loaded()


# Global Instance
block_manager = BlockManager(BlockingRepository())
