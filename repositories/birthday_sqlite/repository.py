"""Birthday settings and member operations over normalized, shared identities."""

import time
from datetime import date
from time import strptime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

import config
from api.birthday_models import BirthdayDelivery, BirthdayGuildConfig, BirthdayUser
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    discord_id,
    ensure_channel,
    ensure_guild,
    ensure_member,
    ensure_role,
)
from repositories.sqlite.schema import (
    birthday_deliveries,
    birthday_history,
    birthday_settings,
    member_birthdays,
)
from utils.birthday_utils import is_birthday_today


async def _read(
    connection: AsyncConnection, key: int | None = None
) -> list[BirthdayGuildConfig]:
    query = select(
        birthday_settings.c.guild_id,
        birthday_settings.c.guild_name_hint,
        birthday_settings.c.channel_id,
        birthday_settings.c.birthday_role_id,
        birthday_settings.c.version,
    )
    if key is not None:
        query = query.where(birthday_settings.c.guild_id == key)
    rows = await connection.execute(query.order_by(birthday_settings.c.guild_id))
    configs = {
        gid: BirthdayGuildConfig(
            gid, name, channel, birthday_role_id=role, version=version
        )
        for gid, name, channel, role, version in rows
    }
    if not configs:
        return []
    members = await connection.execute(
        select(
            member_birthdays.c.guild_id,
            member_birthdays.c.user_id,
            member_birthdays.c.display_name_hint,
            member_birthdays.c.birth_date,
            member_birthdays.c.version,
        )
        .where(member_birthdays.c.guild_id.in_(configs))
        .order_by(member_birthdays.c.guild_id, member_birthdays.c.position)
    )
    for gid, uid, name, birthday, version in members:
        formatted = (
            date.fromisoformat(birthday).strftime(config.DATE_FORMAT)
            if birthday
            else ""
        )
        configs[gid].users[uid] = BirthdayUser(uid, name, formatted, version=version)
    history = await connection.execute(
        select(
            birthday_history.c.guild_id,
            birthday_history.c.user_id,
            birthday_history.c.value,
        )
        .where(birthday_history.c.guild_id.in_(configs))
        .order_by(
            birthday_history.c.guild_id,
            birthday_history.c.user_id,
            birthday_history.c.position,
        )
    )
    for gid, uid, value in history:
        configs[gid].users[uid].was_congrats.append(value)
    return list(configs.values())


class SQLiteBirthdayRepository:
    """Return detached aggregates on reads; mutations target only affected rows.

    The shared database owns transaction admission. Version checks protect stale
    confirmations. No database transaction spans a Discord request.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, key: int) -> BirthdayGuildConfig | None:
        async with self._database.transaction() as connection:
            rows = await _read(connection, key)
            return rows[0] if rows else None

    async def get_all(self) -> list[BirthdayGuildConfig]:
        async with self._database.transaction() as connection:
            return await _read(connection)

    async def get_all_guild_ids(self) -> list[int]:
        async with self._database.transaction() as connection:
            return list(
                await connection.scalars(
                    select(birthday_settings.c.guild_id).order_by(
                        birthday_settings.c.guild_id
                    )
                )
            )

    async def set_user_birthday(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int | None,
        user_id: int,
        user_name: str,
        birthday: str,
    ) -> None:
        """Set a calendar birthday without reloading history or changing settings."""
        birth_date = date(*strptime(birthday, config.DATE_FORMAT)[:3]).isoformat()
        async with self._database.transaction() as connection:
            await ensure_member(connection, guild_id, user_id)
            if channel_id is not None:
                await ensure_channel(connection, channel_id, guild_id)
            await connection.execute(
                insert(birthday_settings)
                .values(
                    guild_id=guild_id,
                    guild_name_hint=server_name,
                    channel_id=channel_id,
                    birthday_role_id=None,
                    version=1,
                )
                .on_conflict_do_nothing()
            )
            position = await connection.scalar(
                select(
                    func.coalesce(func.max(member_birthdays.c.position), -1) + 1
                ).where(member_birthdays.c.guild_id == guild_id)
            )
            statement = insert(member_birthdays).values(
                guild_id=guild_id,
                user_id=user_id,
                birth_date=birth_date,
                display_name_hint=user_name,
                position=position,
                version=1,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        member_birthdays.c.guild_id,
                        member_birthdays.c.user_id,
                    ],
                    set_={
                        "birth_date": birth_date,
                        "display_name_hint": user_name,
                        "version": member_birthdays.c.version + 1,
                    },
                    where=member_birthdays.c.birth_date.is_distinct_from(birth_date)
                    | (member_birthdays.c.display_name_hint != user_name),
                )
            )

    async def configure_guild(
        self,
        guild_id: int,
        server_name: str,
        channel_id: int,
        birthday_role_id: int | None,
    ) -> None:
        """Change delivery settings without replacing members or history."""
        async with self._database.transaction() as connection:
            await ensure_guild(connection, guild_id)
            await ensure_channel(connection, channel_id, guild_id)
            if birthday_role_id is not None:
                await ensure_role(connection, birthday_role_id, guild_id)
            statement = insert(birthday_settings).values(
                guild_id=guild_id,
                guild_name_hint=server_name,
                channel_id=channel_id,
                birthday_role_id=birthday_role_id,
                version=1,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[birthday_settings.c.guild_id],
                    set_={
                        "channel_id": channel_id,
                        "birthday_role_id": birthday_role_id,
                        "version": birthday_settings.c.version + 1,
                    },
                    where=birthday_settings.c.channel_id.is_distinct_from(channel_id)
                    | birthday_settings.c.birthday_role_id.is_distinct_from(
                        birthday_role_id
                    ),
                )
            )

    async def clear_user_birthday(
        self, guild_id: int, user_id: int, *, expected_version: int
    ) -> tuple[bool, bool]:
        """Return (settings exist, cleared), rejecting obsolete confirmations."""
        async with self._database.transaction() as connection:
            exists = await connection.scalar(
                select(birthday_settings.c.guild_id).where(
                    birthday_settings.c.guild_id == guild_id
                )
            )
            changed = await connection.scalar(
                member_birthdays.update()
                .where(
                    member_birthdays.c.guild_id == guild_id,
                    member_birthdays.c.user_id == user_id,
                    member_birthdays.c.version == expected_version,
                    member_birthdays.c.birth_date.is_not(None),
                )
                .values(birth_date=None, version=member_birthdays.c.version + 1)
                .returning(member_birthdays.c.user_id)
            )
            return exists is not None, changed is not None

    async def versions_current(
        self, guild_id: int, user_id: int, settings_version: int, birthday_version: int
    ) -> bool:
        """Recheck detached settings/member intent before an external role update."""
        async with self._database.transaction() as connection:
            return (
                await connection.scalar(
                    select(member_birthdays.c.user_id)
                    .join(
                        birthday_settings,
                        birthday_settings.c.guild_id == member_birthdays.c.guild_id,
                    )
                    .where(
                        member_birthdays.c.guild_id == guild_id,
                        member_birthdays.c.user_id == user_id,
                        member_birthdays.c.version == birthday_version,
                        birthday_settings.c.version == settings_version,
                    )
                )
                is not None
            )

    async def claim_delivery(self, claim: BirthdayDelivery) -> bool:
        """Claim a current due date, replacing only a definitely unsent obsolete claim.

        Replacement changes the operation token atomically. Uncertain and sent
        rows remain closed to retries; obsolete never means an attempted send.
        """
        async with self._database.transaction() as connection:
            if not await _delivery_current(connection, claim):
                return False
            marker = await connection.scalar(
                select(birthday_history.c.position)
                .where(
                    birthday_history.c.guild_id == claim.guild_id,
                    birthday_history.c.user_id == claim.user_id,
                    birthday_history.c.value
                    == claim.today.strftime(config.DATE_FORMAT),
                )
                .limit(1)
            )
            if marker is not None:
                return False
            statement = (
                insert(birthday_deliveries)
                .values(
                    guild_id=claim.guild_id,
                    user_id=claim.user_id,
                    calendar_date=claim.today.isoformat(),
                    operation_id=claim.operation_id,
                    settings_version=claim.settings_version,
                    birthday_version=claim.birthday_version,
                    status="claimed",
                    updated_us=time.time_ns() // 1000,
                )
                .on_conflict_do_update(
                    index_elements=[
                        birthday_deliveries.c.guild_id,
                        birthday_deliveries.c.user_id,
                        birthday_deliveries.c.calendar_date,
                    ],
                    set_={
                        "operation_id": claim.operation_id,
                        "settings_version": claim.settings_version,
                        "birthday_version": claim.birthday_version,
                        "status": "claimed",
                        "message_id": None,
                        "updated_us": time.time_ns() // 1000,
                    },
                    where=(birthday_deliveries.c.status == "obsolete")
                    & (birthday_deliveries.c.operation_id != claim.operation_id),
                )
                .returning(birthday_deliveries.c.operation_id)
            )
            return await connection.scalar(statement) == claim.operation_id

    async def begin_delivery(self, claim: BirthdayDelivery) -> bool:
        """Recheck versions and mark the send as potentially executed.

        A process crash or cancellation after this commit leaves 'uncertain'. It
        must never trigger an automatic second send for the same calendar key.
        """
        async with self._database.transaction() as connection:
            current = await _delivery_current(connection, claim)
            changed = await connection.scalar(
                birthday_deliveries.update()
                .where(
                    birthday_deliveries.c.operation_id == claim.operation_id,
                    birthday_deliveries.c.status == "claimed",
                )
                .values(
                    status="uncertain" if current else "obsolete",
                    updated_us=time.time_ns() // 1000,
                )
                .returning(birthday_deliveries.c.operation_id)
            )
            return current and changed is not None

    async def finish_delivery(self, claim: BirthdayDelivery, message_id: int) -> None:
        """Confirm a send and append its presentation history atomically."""
        discord_id(message_id)
        async with self._database.transaction() as connection:
            changed = await connection.scalar(
                birthday_deliveries.update()
                .where(
                    birthday_deliveries.c.operation_id == claim.operation_id,
                    birthday_deliveries.c.status == "uncertain",
                )
                .values(
                    status="sent",
                    message_id=message_id,
                    updated_us=time.time_ns() // 1000,
                )
                .returning(birthday_deliveries.c.operation_id)
            )
            if changed is None:
                return
            exists = await connection.scalar(
                select(member_birthdays.c.user_id).where(
                    member_birthdays.c.guild_id == claim.guild_id,
                    member_birthdays.c.user_id == claim.user_id,
                )
            )
            if exists is None:
                return
            position = await connection.scalar(
                select(
                    func.coalesce(func.max(birthday_history.c.position), -1) + 1
                ).where(
                    birthday_history.c.guild_id == claim.guild_id,
                    birthday_history.c.user_id == claim.user_id,
                )
            )
            await connection.execute(
                insert(birthday_history).values(
                    guild_id=claim.guild_id,
                    user_id=claim.user_id,
                    position=position,
                    value=claim.today.strftime(config.DATE_FORMAT),
                    origin="confirmed_send",
                )
            )

    async def recover_deliveries(self) -> None:
        """Keep interrupted claims uncertain; do not guess the remote outcome."""
        async with self._database.transaction() as connection:
            await connection.execute(
                birthday_deliveries.update()
                .where(birthday_deliveries.c.status == "claimed")
                .values(status="uncertain", updated_us=time.time_ns() // 1000)
            )


async def _delivery_current(
    connection: AsyncConnection, claim: BirthdayDelivery
) -> bool:
    birthday = await connection.scalar(
        select(member_birthdays.c.birth_date)
        .join(
            birthday_settings,
            birthday_settings.c.guild_id == member_birthdays.c.guild_id,
        )
        .where(
            member_birthdays.c.guild_id == claim.guild_id,
            member_birthdays.c.user_id == claim.user_id,
            member_birthdays.c.version == claim.birthday_version,
            birthday_settings.c.version == claim.settings_version,
        )
    )
    return birthday is not None and is_birthday_today(
        date.fromisoformat(birthday).strftime(config.DATE_FORMAT), claim.today
    )
