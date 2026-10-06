"""Validate shared representations and create minimal identity/context rows."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from repositories.sqlite.schema import (
    channels,
    guild_members,
    guilds,
    roles,
    users,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def discord_id(value: object) -> int:
    """Accept positive signed-64-bit IDs without bool or numeric coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ValueError("Discord ID must be a positive signed-64-bit integer")
    return value


def utc_microseconds(value: datetime) -> int:
    """Convert an aware instant exactly, without a floating-point timestamp."""
    if value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    delta = value.astimezone(UTC) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_microseconds(value: int) -> datetime:
    """Return an aware UTC instant from integer microseconds."""
    return _EPOCH + timedelta(microseconds=value)


async def ensure_guild(connection: AsyncConnection, guild_id: int) -> None:
    """Create identity without inventing a current name or observation time."""
    await connection.execute(
        insert(guilds).values(guild_id=discord_id(guild_id)).on_conflict_do_nothing()
    )


async def ensure_user(connection: AsyncConnection, user_id: int) -> None:
    """Create identity without inferring human/bot state or a username."""
    await connection.execute(
        insert(users).values(user_id=discord_id(user_id)).on_conflict_do_nothing()
    )


async def ensure_member(
    connection: AsyncConnection, guild_id: int, user_id: int
) -> None:
    """Create a known guild context, not a claim of current membership."""
    await ensure_guild(connection, guild_id)
    await ensure_user(connection, user_id)
    await connection.execute(
        insert(guild_members)
        .values(guild_id=guild_id, user_id=user_id)
        .on_conflict_do_nothing()
    )


async def ensure_channel(
    connection: AsyncConnection, channel_id: int, guild_id: int | None
) -> None:
    """Record or resolve channel scope; reject a conflicting known guild."""
    discord_id(channel_id)
    if guild_id is not None:
        await ensure_guild(connection, guild_id)
    await connection.execute(
        insert(channels)
        .values(channel_id=channel_id, guild_id=guild_id)
        .on_conflict_do_nothing()
    )
    known = await connection.scalar(
        select(channels.c.guild_id).where(channels.c.channel_id == channel_id)
    )
    if known is not None and guild_id is not None and known != guild_id:
        raise ValueError("Channel belongs to a different guild")
    if known is None and guild_id is not None:
        await connection.execute(
            channels.update()
            .where(channels.c.channel_id == channel_id)
            .values(guild_id=guild_id)
        )


async def ensure_role(connection: AsyncConnection, role_id: int, guild_id: int) -> None:
    """Create a role identity in exactly one guild without a live API lookup."""
    discord_id(role_id)
    await ensure_guild(connection, guild_id)
    await connection.execute(
        insert(roles)
        .values(role_id=role_id, guild_id=guild_id)
        .on_conflict_do_nothing()
    )
    known = await connection.scalar(
        select(roles.c.guild_id).where(roles.c.role_id == role_id)
    )
    if known != guild_id:
        raise ValueError("Role belongs to a different guild")


async def observe_username(
    connection: AsyncConnection, user_id: int, username: str, observed_us: int
) -> None:
    """Record a directly observed username; historical display hints never call this."""
    await connection.execute(
        users.update()
        .where(
            users.c.user_id == user_id,
            (users.c.observed_us.is_(None)) | (users.c.observed_us <= observed_us),
        )
        .values(username=username, observed_us=observed_us)
    )


async def observe_guild_name(
    connection: AsyncConnection, guild_id: int, name: str, observed_us: int
) -> None:
    """Update current metadata only from an observation at least as recent."""
    await connection.execute(
        guilds.update()
        .where(
            guilds.c.guild_id == guild_id,
            (guilds.c.observed_us.is_(None)) | (guilds.c.observed_us <= observed_us),
        )
        .values(name=name, observed_us=observed_us)
    )
