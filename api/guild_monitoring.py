"""Server monitoring manager for tracking and restoring member roles."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict, cast

import discord
from discord.utils import utcnow

import config
from utils.json_utils import get_json, save_json


class MemberSnapshotDict(TypedDict):
    """Typed dict for serialized member snapshot data."""

    roles: list[int]
    username: str
    left_at: str  # ISO format datetime string


class GuildDataDict(TypedDict):
    """Typed dict for guild configuration and member snapshots."""

    enabled: bool
    ttl_days: int | None
    members: dict[str, MemberSnapshotDict]  # user_id -> snapshot


class RestorationResult(TypedDict):
    """Typed dict for role restoration results."""

    restored: list[discord.Role]
    skipped: list[int]


@dataclass
class MemberSnapshot:
    """Snapshot of a member's roles when they left the server."""

    user_id: int
    username: str
    roles: list[int]
    left_at: datetime

    def to_dict(self) -> MemberSnapshotDict:
        """Serialize to JSON-compatible dict."""
        return {
            "roles": self.roles,
            "username": self.username,
            "left_at": self.left_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, user_id: int, data: MemberSnapshotDict) -> "MemberSnapshot":
        """Deserialize from JSON dict."""
        return cls(
            user_id=user_id,
            username=data["username"],
            roles=data["roles"],
            left_at=datetime.fromisoformat(data["left_at"]),
        )


class ServerMonitoringManager:
    """Manages role snapshots for members who leave servers."""

    def __init__(self, data_dir: Path):
        """Initialize the manager.

        Args:
            data_dir: Directory to store per-guild snapshot files.

        """
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_guild_file(self, guild_id: int) -> Path:
        """Get the file path for a guild's snapshot data."""
        return self.data_dir / f"guild_{guild_id}.json"

    def _get_default_config(self) -> GuildDataDict:
        """Get default configuration for a new guild."""
        return {
            "enabled": False,
            "ttl_days": None,
            "members": {},
        }

    def _load_guild_data(self, guild_id: int) -> GuildDataDict:
        """Load guild data from disk, or return default if not found."""
        data = get_json(self._get_guild_file(guild_id))
        if data is None:
            return self._get_default_config()
        return cast(GuildDataDict, data)

    def _save_guild_data(self, guild_id: int, data: GuildDataDict) -> None:
        """Save guild data to disk."""
        save_json(self._get_guild_file(guild_id), data)

    def is_enabled(self, guild_id: int) -> bool:
        """Check if monitoring is enabled for a guild."""
        data = self._load_guild_data(guild_id)
        return data["enabled"]

    def set_enabled(
        self, guild_id: int, enabled: bool, ttl_days: int | None = None
    ) -> None:
        """Enable or disable monitoring for a guild.

        Args:
            guild_id: The guild ID.
            enabled: Whether to enable monitoring.
            ttl_days: Optional TTL in days. None = infinite.

        """
        data = self._load_guild_data(guild_id)
        data["enabled"] = enabled
        if ttl_days is not None:
            data["ttl_days"] = ttl_days
        self._save_guild_data(guild_id, data)

    def get_ttl(self, guild_id: int) -> int | None:
        """Get the TTL setting for a guild. None = infinite."""
        data = self._load_guild_data(guild_id)
        return data["ttl_days"]

    def save_snapshot(self, member: discord.Member) -> int:
        """Save a snapshot of a member's roles when they leave.

        Args:
            member: The member who is leaving.

        Returns:
            Number of roles saved (0 if monitoring disabled or no saveable roles).

        """
        if member.bot:
            return 0

        guild_id = member.guild.id
        data = self._load_guild_data(guild_id)

        if not data["enabled"]:
            return 0

        saveable_roles = self._filter_saveable_roles(member)
        if not saveable_roles:
            return 0

        snapshot = MemberSnapshot(
            user_id=member.id,
            username=str(member),
            roles=saveable_roles,
            left_at=utcnow(),
        )

        data["members"][str(member.id)] = snapshot.to_dict()
        self._save_guild_data(guild_id, data)

        return len(saveable_roles)

    def get_snapshot(self, guild_id: int, user_id: int) -> MemberSnapshot | None:
        """Get a snapshot for a specific member.

        Args:
            guild_id: The guild ID.
            user_id: The user ID.

        Returns:
            The snapshot if found, otherwise None.

        """
        data = self._load_guild_data(guild_id)
        member_data = data["members"].get(str(user_id))

        if not member_data:
            return None

        return MemberSnapshot.from_dict(user_id, member_data)

    def delete_snapshot(self, guild_id: int, user_id: int) -> bool:
        """Delete a snapshot for a specific member.

        Args:
            guild_id: The guild ID.
            user_id: The user ID.

        Returns:
            True if snapshot existed and was deleted, False otherwise.

        """
        data = self._load_guild_data(guild_id)
        members = data["members"]

        if str(user_id) not in members:
            return False

        del members[str(user_id)]
        self._save_guild_data(guild_id, data)
        return True

    def get_all_snapshots(self, guild_id: int) -> list[MemberSnapshot]:
        """Get all snapshots for a guild, sorted by left_at (newest first).

        Args:
            guild_id: The guild ID.

        Returns:
            List of snapshots.

        """
        data = self._load_guild_data(guild_id)
        members = data["members"]

        snapshots = [
            MemberSnapshot.from_dict(int(user_id), member_data)
            for user_id, member_data in members.items()
        ]

        return sorted(snapshots, key=lambda s: s.left_at, reverse=True)

    def cleanup_expired(self, guild_id: int) -> int:
        """Remove snapshots older than TTL.

        Args:
            guild_id: The guild ID.

        Returns:
            Number of snapshots removed.

        """
        data = self._load_guild_data(guild_id)
        ttl_days = data["ttl_days"]

        if ttl_days is None:
            return 0

        members = data["members"]
        cutoff_date = utcnow() - timedelta(days=ttl_days)

        expired_users = [
            user_id
            for user_id, member_data in members.items()
            if datetime.fromisoformat(member_data["left_at"]) < cutoff_date
        ]

        for user_id in expired_users:
            del members[user_id]

        if expired_users:
            self._save_guild_data(guild_id, data)

        return len(expired_users)

    async def restore_snapshot(
        self, member: discord.Member
    ) -> tuple[list[discord.Role], list[int]]:
        """Restore roles from a snapshot when a member rejoins.

        Args:
            member: The member who rejoined.

        Returns:
            Tuple of (successfully_restored_roles, skipped_role_ids).

        """
        snapshot = self.get_snapshot(member.guild.id, member.id)
        if not snapshot:
            return ([], [])

        guild = member.guild
        restored_roles: list[discord.Role] = []
        skipped_role_ids: list[int] = []

        for role_id in snapshot.roles:
            role = await self._validate_role(guild, role_id)
            if role:
                restored_roles.append(role)
            else:
                skipped_role_ids.append(role_id)

        if restored_roles:
            try:
                await member.add_roles(
                    *restored_roles, reason="Автовосстановление ролей"
                )
            except discord.Forbidden:
                return ([], snapshot.roles)
            except discord.HTTPException:
                return ([], snapshot.roles)

        # Delete snapshot after successful restore
        self.delete_snapshot(member.guild.id, member.id)

        return (restored_roles, skipped_role_ids)

    def _filter_saveable_roles(self, member: discord.Member) -> list[int]:
        """Get only roles that should be saved.

        Excludes:
        - @everyone
        - Managed roles (bots, integrations)
        - Premium subscriber (Nitro boost) roles

        Args:
            member: The member.

        Returns:
            List of role IDs.

        """
        return [
            role.id
            for role in member.roles
            if not role.is_default()
            and not role.managed
            and not role.is_premium_subscriber()
        ]

    async def _validate_role(
        self, guild: discord.Guild, role_id: int
    ) -> discord.Role | None:
        """Validate that a role can be restored.

        Checks:
        - Role still exists
        - Role is not managed
        - Bot has permission to assign it (hierarchy)

        Args:
            guild: The guild.
            role_id: The role ID.

        Returns:
            The role if valid, otherwise None.

        """
        role = guild.get_role(role_id)
        if not role:
            return None

        if role.managed:
            return None

        bot_member = guild.get_member(guild.me.id)
        if not bot_member:
            return None

        return role


monitor_manager = ServerMonitoringManager(config.DATA_DIR / "guild_monitor")
