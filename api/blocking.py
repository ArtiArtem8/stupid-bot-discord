from __future__ import annotations

import logging

import discord
from discord.utils import utcnow

from api.blocking_models import (
    BlockedUser,
    NameHistoryEntry,
)
from repositories.blocking_repository import BlockingRepository

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


# Global Instance
block_manager = BlockManager(BlockingRepository())
