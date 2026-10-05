"""Persist birthday aggregates with typed Core queries and owned transactions."""

from collections.abc import Sequence
from datetime import date
from typing import override

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.base_repository import BaseRepository
from repositories.birthday_sqlite.schema import congratulations, guilds, users


async def _read(
    connection: AsyncConnection, key: int | None = None
) -> list[BirthdayGuildConfig]:
    query = select(
        guilds.c.guild_id,
        guilds.c.server_name,
        guilds.c.channel_id,
        guilds.c.birthday_role_id,
    )
    if key is not None:
        query = query.where(guilds.c.guild_id == key)
    rows = await connection.execute(query.order_by(guilds.c.guild_id))
    configs = {
        gid: BirthdayGuildConfig(gid, name, channel, birthday_role_id=role)
        for gid, name, channel, role in rows
    }
    if configs:
        members = await connection.execute(
            select(
                users.c.guild_id,
                users.c.user_id,
                users.c.name,
                users.c.birthday,
            )
            .where(users.c.guild_id.in_(configs))
            .order_by(users.c.guild_id, users.c.position)
        )
        for gid, uid, name, birthday in members:
            configs[gid].users[uid] = BirthdayUser(uid, name, birthday)
        history = await connection.execute(
            select(
                congratulations.c.guild_id,
                congratulations.c.user_id,
                congratulations.c.value,
            )
            .where(congratulations.c.guild_id.in_(configs))
            .order_by(
                congratulations.c.guild_id,
                congratulations.c.user_id,
                congratulations.c.position,
            )
        )
        for gid, uid, value in history:
            configs[gid].users[uid].was_congrats.append(value)
    return list(configs.values())


async def _save(
    connection: AsyncConnection, entity: BirthdayGuildConfig, key: int
) -> None:
    statement = insert(guilds).values(
        guild_id=key,
        server_name=entity.server_name,
        channel_id=entity.channel_id,
        birthday_role_id=entity.birthday_role_id,
    )
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[guilds.c.guild_id],
            set_={
                "server_name": entity.server_name,
                "channel_id": entity.channel_id,
                "birthday_role_id": entity.birthday_role_id,
            },
        )
    )
    await connection.execute(users.delete().where(users.c.guild_id == key))
    for position, (uid, user) in enumerate(entity.users.items()):
        await connection.execute(
            insert(users).values(
                guild_id=key,
                user_id=uid,
                name=user.name,
                birthday=user.birthday,
                position=position,
            )
        )
        for index, value in enumerate(user.was_congrats):
            await connection.execute(
                insert(congratulations).values(
                    guild_id=key, user_id=uid, position=index, value=value
                )
            )


class SQLiteBirthdayRepository(BaseRepository[BirthdayGuildConfig, int]):
    """Implement birthday operations on one caller-owned birthday engine.

    Use database.open_engine: its queued checkout owns each whole operation.
    Results are detached domain objects. Reads never create schema; errors and
    cancellation propagate after the SQLAlchemy transaction context unwinds.
    This repository does not send Discord messages or promise exactly-once sends.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @override
    async def get(self, key: int) -> BirthdayGuildConfig | None:
        async with self._engine.begin() as connection:
            configs = await _read(connection, key)
            return configs[0] if configs else None

    @override
    async def get_all(self) -> list[BirthdayGuildConfig]:
        async with self._engine.begin() as connection:
            return await _read(connection)

    @override
    async def save(self, entity: BirthdayGuildConfig, key: int | None = None) -> None:
        """Replace an aggregate atomically, matching the existing explicit save API."""
        async with self._engine.begin() as connection:
            await _save(connection, entity, entity.guild_id if key is None else key)

    @override
    async def delete(self, key: int) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(guilds.delete().where(guilds.c.guild_id == key))

    async def get_all_guild_ids(self) -> list[int]:
        async with self._engine.begin() as connection:
            return list(
                (
                    await connection.scalars(
                        select(guilds.c.guild_id).order_by(guilds.c.guild_id)
                    )
                ).all()
            )

    async def set_user_birthday(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int,
        user_id: int,
        user_name: str,
        birthday: str,
    ) -> BirthdayGuildConfig:
        """Update one member, preserving existing delivery settings and history."""
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(guilds)
                .values(
                    guild_id=guild_id,
                    server_name=server_name,
                    channel_id=channel_id,
                    birthday_role_id=None,
                )
                .on_conflict_do_nothing()
            )
            existing = (await _read(connection, guild_id))[0]
            statement = insert(users).values(
                guild_id=guild_id,
                user_id=user_id,
                name=user_name,
                birthday=birthday,
                position=len(existing.users),
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[users.c.guild_id, users.c.user_id],
                    set_={"name": user_name, "birthday": birthday},
                )
            )
            return (await _read(connection, guild_id))[0]

    async def configure_guild(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int,
        birthday_role_id: int | None,
    ) -> BirthdayGuildConfig:
        """Change delivery settings, preserving an existing guild name and members."""
        async with self._engine.begin() as connection:
            statement = insert(guilds).values(
                guild_id=guild_id,
                server_name=server_name,
                channel_id=channel_id,
                birthday_role_id=birthday_role_id,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[guilds.c.guild_id],
                    set_={
                        "channel_id": channel_id,
                        "birthday_role_id": birthday_role_id,
                    },
                )
            )
            return (await _read(connection, guild_id))[0]

    async def clear_user_birthday(
        self, guild_id: int, user_id: int
    ) -> tuple[bool, bool]:
        """Return (guild exists, birthday was present), retaining member history."""
        async with self._engine.begin() as connection:
            configs = await _read(connection, guild_id)
            if not configs:
                return False, False
            user = configs[0].get_user(user_id)
            if user is None or not user.has_birthday():
                return True, False
            await connection.execute(
                users.update()
                .where(users.c.guild_id == guild_id, users.c.user_id == user_id)
                .values(birthday="")
            )
            return True, True

    async def record_congratulation(
        self, guild_id: int, user_id: int, congratulation_date: date
    ) -> bool:
        """Append a sent marker once; the queued transaction owns read and update."""
        async with self._engine.begin() as connection:
            configs = await _read(connection, guild_id)
            if not configs:
                return False
            user = configs[0].get_user(user_id)
            if user is None or user.was_congratulated_today(congratulation_date):
                return False
            user.add_congratulation(congratulation_date)
            await connection.execute(
                insert(congratulations).values(
                    guild_id=guild_id,
                    user_id=user_id,
                    position=len(user.was_congrats) - 1,
                    value=user.was_congrats[-1],
                )
            )
            return True

    async def import_guilds(self, configs: Sequence[BirthdayGuildConfig]) -> int:
        """Import atomically, accepting identical repeats and rejecting conflicts.

        Member insertion order is part of import identity. Differing aggregates abort
        the entire import rather than
        overwriting changes made since a previous import. Return inserted guilds.
        """
        inserted = 0
        async with self._engine.begin() as connection:
            for config in configs:
                existing = await _read(connection, config.guild_id)
                if existing:
                    if existing[0] != config or list(existing[0].users) != list(
                        config.users
                    ):
                        raise ValueError(
                            f"Birthday import conflicts with guild {config.guild_id}"
                        )
                    continue
                await _save(connection, config, config.guild_id)
                inserted += 1
        return inserted
