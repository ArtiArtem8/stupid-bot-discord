"""Server monitoring manager for tracking and restoring member roles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict, cast

import discord
from discord.utils import utcnow

import config
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject, JsonValue, is_json_object

logger = logging.getLogger(__name__)
_MALFORMED_SNAPSHOT_LOG = (
    "Skipping malformed monitoring snapshot for guild %s user key %s"
)


class MemberSnapshotDict(TypedDict):
    roles: list[int]
    username: str
    left_at: str


@dataclass(slots=True)
class GuildData:
    enabled: bool = False
    ttl_days: int | None = None
    members: JsonObject = field(default_factory=dict[str, JsonValue])


@dataclass(slots=True)
class MemberSnapshot:
    """Snapshot of a member's roles when they left the server."""

    user_id: int
    username: str
    roles: list[int]
    left_at: datetime

    def to_dict(self) -> MemberSnapshotDict:
        return {
            "roles": self.roles,
            "username": self.username,
            "left_at": self.left_at.isoformat(),
        }


def _decode_guild_data(data: JsonObject) -> GuildData:
    if not data:
        return GuildData()

    enabled = data.get("enabled")
    ttl_days = data.get("ttl_days")
    members = data.get("members")
    if not isinstance(enabled, bool):
        raise ValueError("Monitoring data has an invalid enabled value")
    if isinstance(ttl_days, bool) or (
        ttl_days is not None and not isinstance(ttl_days, int)
    ):
        raise ValueError("Monitoring data has an invalid TTL")
    if not isinstance(members, dict):
        raise ValueError("Monitoring data has an invalid members object")
    return GuildData(enabled, ttl_days, members)


def _decode_role_ids(raw: object) -> list[int] | None:
    if not isinstance(raw, list):
        return None

    role_ids: list[int] = []
    for role_id in cast(list[object], raw):
        if isinstance(role_id, bool) or not isinstance(role_id, int):
            return None
        role_ids.append(role_id)

    return role_ids


def _decode_snapshot(user_key: str, raw: object) -> MemberSnapshot | None:
    if not user_key.isdigit() or not is_json_object(raw):
        return None

    role_ids = _decode_role_ids(raw.get("roles"))
    if role_ids is None:
        return None

    username = raw.get("username")
    left_at_raw = raw.get("left_at")
    if not isinstance(username, str) or not isinstance(left_at_raw, str):
        return None

    try:
        left_at = datetime.fromisoformat(left_at_raw)
    except ValueError:
        return None

    if left_at.tzinfo is None:
        return None

    return MemberSnapshot(
        user_id=int(user_key),
        username=username,
        roles=role_ids,
        left_at=left_at,
    )


def _write_guild_data(data: JsonObject, guild_data: GuildData) -> None:
    data["enabled"] = guild_data.enabled
    data["ttl_days"] = guild_data.ttl_days
    data["members"] = guild_data.members


class ServerMonitoringManager:
    """Manage per-guild role snapshots through serialized async stores."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[int, AsyncJsonFileStore] = {}

    def _get_guild_file(self, guild_id: int) -> Path:
        return self.data_dir / f"guild_{guild_id}.json"

    def _get_store(self, guild_id: int) -> AsyncJsonFileStore:
        store = self._stores.get(guild_id)
        if store is None:
            store = AsyncJsonFileStore(self._get_guild_file(guild_id))
            self._stores[guild_id] = store
        return store

    async def _load_guild_data(self, guild_id: int) -> GuildData:
        return _decode_guild_data(await self._get_store(guild_id).read())

    async def is_enabled(self, guild_id: int) -> bool:
        return (await self._load_guild_data(guild_id)).enabled

    async def set_enabled(
        self, guild_id: int, enabled: bool, ttl_days: int | None = None
    ) -> None:
        """Set monitoring state, treating an omitted enable TTL as infinite."""

        def _updater(data: JsonObject) -> None:
            guild_data = _decode_guild_data(data)
            guild_data.enabled = enabled
            if enabled or ttl_days is not None:
                guild_data.ttl_days = ttl_days
            _write_guild_data(data, guild_data)

        await self._get_store(guild_id).update(_updater)

    async def get_ttl(self, guild_id: int) -> int | None:
        return (await self._load_guild_data(guild_id)).ttl_days

    async def save_snapshot(self, member: discord.Member) -> int:
        if member.bot:
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
        saved = False

        def _updater(data: JsonObject) -> None:
            nonlocal saved
            guild_data = _decode_guild_data(data)
            if not guild_data.enabled:
                return
            guild_data.members[str(member.id)] = cast(
                JsonValue, cast(object, snapshot.to_dict())
            )
            _write_guild_data(data, guild_data)
            saved = True

        await self._get_store(member.guild.id).update(_updater)
        return len(saveable_roles) if saved else 0

    async def get_snapshot(self, guild_id: int, user_id: int) -> MemberSnapshot | None:
        guild_data = await self._load_guild_data(guild_id)
        user_key = str(user_id)
        raw = guild_data.members.get(user_key)
        if raw is None:
            return None
        snapshot = _decode_snapshot(user_key, raw)
        if snapshot is None:
            logger.warning(
                "Skipping malformed monitoring snapshot for guild %s user %s",
                guild_id,
                user_id,
            )
        return snapshot

    async def delete_snapshot(
        self,
        guild_id: int,
        user_id: int,
        *,
        expected_left_at: datetime | None = None,
    ) -> bool:
        deleted = False

        def _updater(data: JsonObject) -> None:
            nonlocal deleted
            guild_data = _decode_guild_data(data)
            user_key = str(user_id)
            raw = guild_data.members.get(user_key)
            if raw is None:
                return
            if expected_left_at is not None:
                current = _decode_snapshot(user_key, raw)
                if current is None or current.left_at != expected_left_at:
                    return
            del guild_data.members[user_key]
            _write_guild_data(data, guild_data)
            deleted = True

        await self._get_store(guild_id).update(_updater)
        return deleted

    async def get_all_snapshots(self, guild_id: int) -> list[MemberSnapshot]:
        guild_data = await self._load_guild_data(guild_id)
        snapshots: list[MemberSnapshot] = []
        for user_key, raw in guild_data.members.items():
            snapshot = _decode_snapshot(user_key, raw)
            if snapshot is None:
                logger.warning(
                    _MALFORMED_SNAPSHOT_LOG,
                    guild_id,
                    user_key,
                )
                continue
            snapshots.append(snapshot)
        return sorted(snapshots, key=lambda snapshot: snapshot.left_at, reverse=True)

    async def cleanup_expired(self, guild_id: int) -> int:
        removed = 0

        def _updater(data: JsonObject) -> None:
            nonlocal removed
            guild_data = _decode_guild_data(data)
            if guild_data.ttl_days is None:
                return
            cutoff_date = utcnow() - timedelta(days=guild_data.ttl_days)
            expired: list[str] = []
            for user_key, raw in guild_data.members.items():
                snapshot = _decode_snapshot(user_key, raw)
                if snapshot is None:
                    logger.warning(
                        _MALFORMED_SNAPSHOT_LOG,
                        guild_id,
                        user_key,
                    )
                    continue
                if snapshot.left_at < cutoff_date:
                    expired.append(user_key)
            for user_key in expired:
                del guild_data.members[user_key]
            if expired:
                _write_guild_data(data, guild_data)
            removed = len(expired)

        await self._get_store(guild_id).update(_updater)
        return removed

    async def restore_snapshot(
        self, member: discord.Member
    ) -> tuple[list[discord.Role], list[int]]:
        snapshot = await self.get_snapshot(member.guild.id, member.id)
        if snapshot is None:
            return ([], [])

        restored_roles: list[discord.Role] = []
        skipped_role_ids: list[int] = []
        for role_id in snapshot.roles:
            role = await self._validate_role(member.guild, role_id)
            if role:
                restored_roles.append(role)
            else:
                skipped_role_ids.append(role_id)

        if restored_roles:
            try:
                await member.add_roles(
                    *restored_roles,
                    reason="Автовосстановление ролей",
                )
            except (discord.Forbidden, discord.HTTPException):
                return ([], snapshot.roles)

        await self.delete_snapshot(
            member.guild.id,
            member.id,
            expected_left_at=snapshot.left_at,
        )
        return (restored_roles, skipped_role_ids)

    def _filter_saveable_roles(self, member: discord.Member) -> list[int]:
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
        role = guild.get_role(role_id)
        if role is None or role.managed:
            return None
        if guild.get_member(guild.me.id) is None:
            return None
        return role


monitor_manager = ServerMonitoringManager(config.DATA_DIR / "guild_monitor")
