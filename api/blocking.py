"""Guild access policy backed by atomic block-state and audit operations."""

import discord
from discord.utils import utcnow

from api.blocking_models import BlockedUser
from repositories.blocking_repository import BlockingRepository


class BlockManager:
    """Apply observed member details without splitting a block transaction."""

    def __init__(self, repository: BlockingRepository) -> None:
        self.repo = repository

    async def is_user_blocked(self, guild_id: int, user_id: int) -> bool:
        return await self.repo.is_blocked(guild_id, user_id)

    async def get_guild_users(self, guild_id: int) -> list[BlockedUser]:
        return await self.repo.get_all_for_guild(guild_id)

    async def get_user(self, guild_id: int, user_id: int) -> BlockedUser | None:
        return await self.repo.get((guild_id, user_id))

    async def block_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> bool:
        return await self.repo.change(
            guild_id,
            target.id,
            blocked=True,
            display_name=target.display_name,
            username=target.name,
            admin_id=admin_id,
            reason=reason,
            observed_at=utcnow(),
        )

    async def unblock_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> bool:
        return await self.repo.change(
            guild_id,
            target.id,
            blocked=False,
            display_name=target.display_name,
            username=target.name,
            admin_id=admin_id,
            reason=reason,
            observed_at=utcnow(),
        )
