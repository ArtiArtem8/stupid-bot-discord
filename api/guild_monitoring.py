"""Restore member roles while owning per-member network operation ordering."""

import asyncio
import logging
from datetime import timedelta
from weakref import WeakValueDictionary

import discord
from discord.utils import utcnow

from api.monitor_models import MemberSnapshot
from repositories.monitor_repository import MonitorRepository

logger = logging.getLogger(__name__)


class ServerMonitoringManager:
    """Serialize leave/restore for a member; SQL snapshots outlive Discord cache."""

    def __init__(self, repository: MonitorRepository) -> None:
        self.repository = repository
        self._member_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
            WeakValueDictionary()
        )

    def _member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        # A restore may await Discord while a new leave arrives. Both operations
        # retain this lock; snapshot identity also fences independent DB cleanup.
        key = (guild_id, user_id)
        lock = self._member_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._member_locks[key] = lock
        return lock

    async def is_enabled(self, guild_id: int) -> bool:
        return (await self.repository.settings(guild_id)).enabled

    async def set_enabled(
        self, guild_id: int, enabled: bool, ttl_days: int | None = None
    ) -> None:
        await self.repository.set_enabled(guild_id, enabled, ttl_days)

    async def get_ttl(self, guild_id: int) -> int | None:
        return (await self.repository.settings(guild_id)).ttl_days

    async def get_snapshot(self, guild_id: int, user_id: int) -> MemberSnapshot | None:
        rows = await self.repository.snapshots(guild_id, user_id)
        return rows[0] if rows else None

    async def get_all_snapshots(self, guild_id: int) -> list[MemberSnapshot]:
        return await self.repository.snapshots(guild_id)

    async def save_snapshot(self, member: discord.Member) -> int:
        if member.bot:
            return 0
        async with self._member_lock(member.guild.id, member.id):
            roles = self._filter_saveable_roles(member)
            saved = await self.repository.save(
                member.guild.id, member.id, str(member), roles, utcnow()
            )
            return len(roles) if saved else 0

    async def delete_snapshot(
        self, guild_id: int, user_id: int, *, expected: MemberSnapshot | None = None
    ) -> bool:
        async with self._member_lock(guild_id, user_id):
            snapshot = expected or await self.get_snapshot(guild_id, user_id)
            return (
                await self.repository.delete(guild_id, snapshot) if snapshot else False
            )

    async def cleanup_expired(self, guild_id: int) -> int:
        return await self.repository.cleanup_expired(guild_id, utcnow())

    async def restore_snapshot(
        self, member: discord.Member
    ) -> tuple[list[discord.Role], list[int]]:
        """Keep failed roles retryable; delete only the snapshot actually restored."""
        async with self._member_lock(member.guild.id, member.id):
            snapshot = await self.get_snapshot(member.guild.id, member.id)
            if snapshot is None:
                return [], []
            ttl = await self.get_ttl(member.guild.id)
            if ttl is not None and snapshot.left_at < utcnow() - timedelta(days=ttl):
                await self.repository.delete(member.guild.id, snapshot)
                return [], []
            restored, skipped, retry_needed = await self._restore_roles(
                member, snapshot
            )
            if not retry_needed:
                await self.repository.delete(member.guild.id, snapshot)
            return restored, skipped

    async def _restore_roles(
        self, member: discord.Member, snapshot: MemberSnapshot
    ) -> tuple[list[discord.Role], list[int], bool]:
        restored_roles: list[discord.Role] = []
        skipped_role_ids: list[int] = []
        retry_needed = False
        for role_id in snapshot.roles:
            if not await self.repository.is_current(
                member.guild.id, snapshot, utcnow()
            ):
                break
            role = member.guild.get_role(role_id)
            if role is None:
                skipped_role_ids.append(role_id)
                continue
            if role in member.roles:
                continue
            if not self._can_assign_role(member.guild, role):
                skipped_role_ids.append(role_id)
                retry_needed = True
                continue
            try:
                await member.add_roles(role, reason="Автовосстановление ролей")
            except discord.HTTPException as error:
                skipped_role_ids.append(role_id)
                retry_needed = True
                logger.warning(
                    "Role restore failed in guild %s for user %s role %s: HTTP %s",
                    member.guild.id,
                    member.id,
                    role_id,
                    error.status,
                )
                logger.debug("Role restore request traceback", exc_info=True)
            else:
                restored_roles.append(role)

        return (restored_roles, skipped_role_ids, retry_needed)

    def _filter_saveable_roles(self, member: discord.Member) -> list[int]:
        return [
            role.id
            for role in member.roles
            if not role.is_default()
            and not role.managed
            and not role.is_premium_subscriber()
        ]

    def _can_assign_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        return bool(
            guild.me
            and guild.me.guild_permissions.manage_roles
            and role.is_assignable()
        )
