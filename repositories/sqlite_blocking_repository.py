"""Atomic guild-scoped access state and ordered administrator/name observations."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from api.blocking_models import BlockedUser, BlockHistoryEntry, NameHistoryEntry
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    ensure_member,
    ensure_user,
    from_microseconds,
    observe_username,
    utc_microseconds,
)
from repositories.sqlite.schema import (
    block_events,
    member_blocks,
    member_name_observations,
)


async def _read(
    connection: AsyncConnection, guild_id: int, user_id: int
) -> BlockedUser | None:
    row = (
        await connection.execute(
            select(
                member_blocks.c.display_name_hint,
                member_blocks.c.username_hint,
                member_blocks.c.blocked,
            ).where(
                member_blocks.c.guild_id == guild_id, member_blocks.c.user_id == user_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    display, username, blocked = row
    user = BlockedUser(user_id, display, username, blocked=blocked)
    events = await connection.execute(
        select(
            block_events.c.action,
            block_events.c.admin_id,
            block_events.c.reason,
            block_events.c.created_us,
        )
        .where(block_events.c.guild_id == guild_id, block_events.c.user_id == user_id)
        .order_by(block_events.c.action, block_events.c.ordinal)
    )
    for action, admin, reason, moment in events:
        history = user.block_history if action == "block" else user.unblock_history
        history.append(BlockHistoryEntry(admin, reason, from_microseconds(moment)))
    names = await connection.execute(
        select(
            member_name_observations.c.display_name,
            member_name_observations.c.created_us,
        )
        .where(
            member_name_observations.c.guild_id == guild_id,
            member_name_observations.c.user_id == user_id,
        )
        .order_by(member_name_observations.c.ordinal)
    )
    user.name_history = [
        NameHistoryEntry(name, from_microseconds(moment)) for name, moment in names
    ]
    return user


class SQLiteBlockingRepository:
    """Change access state and its audit on one connection; reads never fail open."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def is_blocked(self, guild_id: int, user_id: int) -> bool:
        """Read committed access state; propagate storage failures to authorization."""
        async with self._database.transaction() as connection:
            value = await connection.scalar(
                select(member_blocks.c.blocked).where(
                    member_blocks.c.guild_id == guild_id,
                    member_blocks.c.user_id == user_id,
                )
            )
            return value is True

    async def get(self, key: tuple[int, int]) -> BlockedUser | None:
        """Read a detached history in one snapshot transaction."""
        async with self._database.transaction() as connection:
            return await _read(connection, *key)

    async def get_all_for_guild(self, guild_id: int) -> list[BlockedUser]:
        """Return tracked members for the administrator's history view."""
        async with self._database.transaction() as connection:
            ids = await connection.scalars(
                select(member_blocks.c.user_id)
                .where(member_blocks.c.guild_id == guild_id)
                .order_by(member_blocks.c.user_id)
            )
            result: list[BlockedUser] = []
            for user_id in ids:
                user = await _read(connection, guild_id, user_id)
                if user is not None:
                    result.append(user)
            return result

    async def change(
        self,
        guild_id: int,
        user_id: int,
        *,
        blocked: bool,
        display_name: str,
        username: str | None,
        admin_id: int,
        reason: str,
        observed_at: datetime,
    ) -> bool:
        """Commit a state transition, audit and changed display hint atomically.

        Repeating the current state does not append another logical block/unblock.
        Legacy action lists retain their own ordinals; runtime event IDs order
        newly committed transitions without inventing order between old lists.
        """
        async with self._database.transaction() as connection:
            await ensure_member(connection, guild_id, user_id)
            await ensure_user(connection, admin_id)
            moment = utc_microseconds(observed_at)
            if username is not None:
                await observe_username(connection, user_id, username, moment)
            await _observe_name(
                connection, guild_id, user_id, display_name, username, moment
            )
            changed = await connection.scalar(
                member_blocks.update()
                .where(
                    member_blocks.c.guild_id == guild_id,
                    member_blocks.c.user_id == user_id,
                    member_blocks.c.blocked != blocked,
                )
                .values(blocked=blocked, version=member_blocks.c.version + 1)
                .returning(member_blocks.c.version)
            )
            if changed is not None:
                action = "block" if blocked else "unblock"
                position = await connection.scalar(
                    select(
                        func.coalesce(func.max(block_events.c.ordinal), -1) + 1
                    ).where(
                        block_events.c.guild_id == guild_id,
                        block_events.c.user_id == user_id,
                        block_events.c.action == action,
                    )
                )
                await connection.execute(
                    insert(block_events).values(
                        guild_id=guild_id,
                        user_id=user_id,
                        action=action,
                        ordinal=position,
                        admin_id=admin_id,
                        reason=reason,
                        created_us=moment,
                        origin="observed",
                    )
                )
            return changed is not None


async def _observe_name(
    connection: AsyncConnection,
    guild_id: int,
    user_id: int,
    display_name: str,
    username: str | None,
    moment: int,
) -> None:
    previous = (
        await connection.execute(
            select(
                member_blocks.c.display_name_hint, member_blocks.c.username_hint
            ).where(
                member_blocks.c.guild_id == guild_id, member_blocks.c.user_id == user_id
            )
        )
    ).one_or_none()
    if (
        previous is not None
        and previous[0] == display_name
        and (username is None or previous[1] == username)
    ):
        return
    statement = insert(member_blocks).values(
        guild_id=guild_id,
        user_id=user_id,
        display_name_hint=display_name,
        username_hint=username,
        blocked=False,
        version=1,
    )
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[member_blocks.c.guild_id, member_blocks.c.user_id],
            set_={
                "display_name_hint": display_name,
                "username_hint": username
                if username is not None
                else member_blocks.c.username_hint,
                "version": member_blocks.c.version + 1,
            },
        )
    )
    position = await connection.scalar(
        select(
            func.coalesce(func.max(member_name_observations.c.ordinal), -1) + 1
        ).where(
            member_name_observations.c.guild_id == guild_id,
            member_name_observations.c.user_id == user_id,
        )
    )
    await connection.execute(
        insert(member_name_observations).values(
            guild_id=guild_id,
            user_id=user_id,
            ordinal=position,
            display_name=display_name,
            created_us=moment,
        )
    )
