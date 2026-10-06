"""Store role snapshots atomically and delete only an explicitly observed version."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from api.monitor_models import MemberSnapshot, MonitorSettings
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    ensure_guild,
    ensure_member,
    ensure_role,
    from_microseconds,
    utc_microseconds,
)
from repositories.sqlite.schema import (
    monitor_settings,
    role_snapshot_roles,
    role_snapshots,
)

_MICROSECONDS_PER_DAY = 86_400_000_000


class MonitorRepository:
    """Own settings, snapshot replacement and compare-and-delete transactions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def settings(self, guild_id: int) -> MonitorSettings:
        async with self._database.transaction() as connection:
            row = (
                await connection.execute(
                    select(
                        monitor_settings.c.enabled, monitor_settings.c.ttl_days
                    ).where(monitor_settings.c.guild_id == guild_id)
                )
            ).one_or_none()
            if row is None:
                return MonitorSettings()
            enabled, ttl_days = row
            return MonitorSettings(enabled, ttl_days)

    async def set_enabled(
        self, guild_id: int, enabled: bool, ttl_days: int | None = None
    ) -> None:
        if ttl_days is not None and (isinstance(ttl_days, bool) or ttl_days <= 0):
            raise ValueError("Snapshot TTL must be positive")
        async with self._database.transaction() as connection:
            await ensure_guild(connection, guild_id)
            statement = insert(monitor_settings).values(
                guild_id=guild_id, enabled=enabled, ttl_days=ttl_days, version=1
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[monitor_settings.c.guild_id],
                    set_={
                        "enabled": enabled,
                        "ttl_days": ttl_days
                        if enabled or ttl_days is not None
                        else monitor_settings.c.ttl_days,
                        "version": monitor_settings.c.version + 1,
                    },
                )
            )

    async def save(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        roles: list[int],
        left_at: datetime,
    ) -> bool:
        """Replace the complete role snapshot only while monitoring is enabled."""
        moment = utc_microseconds(left_at)
        async with self._database.transaction() as connection:
            enabled = await connection.scalar(
                select(monitor_settings.c.enabled).where(
                    monitor_settings.c.guild_id == guild_id
                )
            )
            if not enabled:
                return False
            await ensure_member(connection, guild_id, user_id)
            for role_id in roles:
                await ensure_role(connection, role_id, guild_id)
            await connection.execute(
                role_snapshots.delete().where(
                    role_snapshots.c.guild_id == guild_id,
                    role_snapshots.c.user_id == user_id,
                )
            )
            snapshot_id = (
                await connection.execute(
                    insert(role_snapshots)
                    .values(
                        guild_id=guild_id,
                        user_id=user_id,
                        username_at_leave=username,
                        left_us=moment,
                        version=1,
                    )
                    .returning(role_snapshots.c.snapshot_id)
                )
            ).scalar_one()
            for position, role_id in enumerate(roles):
                await connection.execute(
                    insert(role_snapshot_roles).values(
                        snapshot_id=snapshot_id,
                        guild_id=guild_id,
                        position=position,
                        role_id=role_id,
                    )
                )
            return True

    async def snapshots(
        self, guild_id: int, user_id: int | None = None
    ) -> list[MemberSnapshot]:
        """Read headers and ordered roles from one consistent snapshot, without N+1."""
        async with self._database.transaction() as connection:
            query = select(
                role_snapshots.c.snapshot_id,
                role_snapshots.c.user_id,
                role_snapshots.c.username_at_leave,
                role_snapshots.c.left_us,
                role_snapshots.c.version,
            ).where(role_snapshots.c.guild_id == guild_id)
            if user_id is not None:
                query = query.where(role_snapshots.c.user_id == user_id)
            rows = await connection.execute(
                query.order_by(
                    role_snapshots.c.left_us.desc(), role_snapshots.c.snapshot_id.desc()
                )
            )
            snapshots = {
                snapshot_id: MemberSnapshot(
                    user_id=member_id,
                    username=name,
                    roles=[],
                    left_at=from_microseconds(left_us),
                    snapshot_id=snapshot_id,
                    version=version,
                )
                for snapshot_id, member_id, name, left_us, version in rows
            }
            if not snapshots:
                return []
            role_rows = await connection.execute(
                select(role_snapshot_roles.c.snapshot_id, role_snapshot_roles.c.role_id)
                .where(role_snapshot_roles.c.snapshot_id.in_(snapshots))
                .order_by(
                    role_snapshot_roles.c.snapshot_id,
                    role_snapshot_roles.c.position,
                )
            )
            for snapshot_id, role_id in role_rows:
                snapshots[snapshot_id].roles.append(role_id)
            return list(snapshots.values())

    async def is_current(
        self, guild_id: int, snapshot: MemberSnapshot, now: datetime
    ) -> bool:
        """Recheck identity and current retention before another remote role request."""
        moment = utc_microseconds(now)
        async with self._database.transaction() as connection:
            current_id = await connection.scalar(
                select(role_snapshots.c.snapshot_id)
                .join(
                    monitor_settings,
                    monitor_settings.c.guild_id == role_snapshots.c.guild_id,
                )
                .where(
                    role_snapshots.c.guild_id == guild_id,
                    role_snapshots.c.user_id == snapshot.user_id,
                    role_snapshots.c.snapshot_id == snapshot.snapshot_id,
                    role_snapshots.c.version == snapshot.version,
                    (monitor_settings.c.ttl_days.is_(None))
                    | (
                        role_snapshots.c.left_us
                        >= moment - monitor_settings.c.ttl_days * _MICROSECONDS_PER_DAY
                    ),
                )
            )
            return current_id is not None

    async def delete(self, guild_id: int, snapshot: MemberSnapshot) -> bool:
        """Protect replacements even when they have the same leave timestamp."""
        async with self._database.transaction() as connection:
            deleted_id = await connection.scalar(
                role_snapshots.delete()
                .where(
                    role_snapshots.c.guild_id == guild_id,
                    role_snapshots.c.user_id == snapshot.user_id,
                    role_snapshots.c.snapshot_id == snapshot.snapshot_id,
                    role_snapshots.c.version == snapshot.version,
                )
                .returning(role_snapshots.c.snapshot_id)
            )
            return deleted_id is not None

    async def cleanup_expired(self, guild_id: int, now: datetime) -> int:
        """Apply the current TTL to committed snapshots within one transaction."""
        moment = utc_microseconds(now)
        async with self._database.transaction() as connection:
            ttl = await connection.scalar(
                select(monitor_settings.c.ttl_days).where(
                    monitor_settings.c.guild_id == guild_id
                )
            )
            if ttl is None:
                return 0
            removed = await connection.scalars(
                role_snapshots.delete()
                .where(
                    role_snapshots.c.guild_id == guild_id,
                    role_snapshots.c.left_us < moment - ttl * _MICROSECONDS_PER_DAY,
                )
                .returning(role_snapshots.c.snapshot_id)
            )
            return len(removed.all())
