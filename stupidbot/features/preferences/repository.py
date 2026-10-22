"""Commit settings edits and optional reminder allowances in the shared database."""

from collections.abc import Collection
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Table, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from stupidbot.features.preferences.models import (
    PreferenceConflictError,
    Preferences,
    PreferenceScope,
    ReminderReservation,
)
from stupidbot.i18n.policy import Language
from stupidbot.storage.database import Database
from stupidbot.storage.identity import (
    discord_id,
    ensure_guild,
    ensure_user,
    utc_microseconds,
)
from stupidbot.storage.schema import (
    PreferencesColumns,
    guild_preferences,
    preference_reminders,
    user_preferences,
)

_REMINDER_INTERVAL_US = int(timedelta(days=14).total_seconds()) * 1_000_000


class PreferencesRepository:
    """Own CAS edits and durable at-most-once prompt reservations.

    The shared Database serializes transactions, including read/modify/write.
    No SQL checkout survives a Discord call. Reminder mutations never change a
    preference revision; uncertain prompt sends keep their consumed allowance.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, scope: PreferenceScope, subject_id: int) -> Preferences:
        """Return defaults without inserting a row or inferring installation age."""
        discord_id(subject_id)
        table = user_preferences if scope is PreferenceScope.USER else guild_preferences
        async with self._database.transaction() as connection:
            return await _read_preferences(connection, table, subject_id)

    async def user_timezones(self, user_ids: Collection[int]) -> dict[int, str]:
        """Return personal timezone overrides in one read.

        Users without an override are absent and inherit the caller's fallback.
        """
        async with self._database.transaction() as connection:
            rows = await connection.execute(
                select(
                    user_preferences.c.subject_id, user_preferences.c.timezone
                ).where(
                    user_preferences.c.subject_id.in_(user_ids),
                    user_preferences.c.timezone.is_not(None),
                )
            )
            return {
                subject_id: timezone
                for subject_id, timezone in rows
                if timezone is not None
            }

    async def save(
        self, scope: PreferenceScope, subject_id: int, proposed: Preferences
    ) -> Preferences:
        """Commit both overrides only if the editor's revision is still current.

        No-op saves preserve revision. A stale editor conflicts even when it
        proposes the latest values. Success is returned only after commit.
        """
        discord_id(subject_id)
        table = user_preferences if scope is PreferenceScope.USER else guild_preferences
        async with self._database.transaction() as connection:
            current = await _read_preferences(connection, table, subject_id)
            if current.revision != proposed.revision:
                raise PreferenceConflictError(current)
            if scope is PreferenceScope.USER:
                await _complete_setup(connection, subject_id)
            if current == proposed:
                return current
            if scope is PreferenceScope.USER:
                await ensure_user(connection, subject_id)
            else:
                await ensure_guild(connection, subject_id)
            saved = Preferences(
                proposed.language, proposed.timezone, current.revision + 1
            )
            statement = insert(table).values(
                subject_id=subject_id,
                language=saved.language,
                timezone=saved.timezone,
                revision=saved.revision,
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[table.c.subject_id],
                    set_={
                        "language": saved.language,
                        "timezone": saved.timezone,
                        "revision": saved.revision,
                    },
                )
            )
        return saved

    async def reserve_prompt_if_due(
        self, user_id: int, now: datetime
    ) -> ReminderReservation | None:
        """Count one successful personal profile use and reserve a due prompt."""
        now_us = utc_microseconds(now)
        table = preference_reminders
        async with self._database.transaction() as connection:
            await ensure_user(connection, user_id)
            initial = insert(table).values(
                user_id=user_id,
                eligible_uses=1,
                prompt_count=0,
                last_prompt_us=None,
                dismissed=False,
                token=None,
            )
            await connection.execute(
                initial.on_conflict_do_update(
                    index_elements=[table.c.user_id],
                    set_={"eligible_uses": table.c.eligible_uses + 1},
                )
            )
            token = uuid4().hex
            result = await connection.execute(
                table.update()
                .where(
                    table.c.user_id == user_id,
                    table.c.eligible_uses >= 2,
                    table.c.prompt_count < 2,
                    table.c.dismissed.is_(False),
                    (table.c.last_prompt_us.is_(None))
                    | (table.c.last_prompt_us <= now_us - _REMINDER_INTERVAL_US),
                )
                .values(
                    prompt_count=table.c.prompt_count + 1,
                    last_prompt_us=now_us,
                    token=token,
                )
                .returning(table.c.user_id)
            )
            reserved = result.scalar_one_or_none() is not None
        return ReminderReservation(user_id, token) if reserved else None

    async def answer_prompt(
        self, reservation: ReminderReservation, *, never: bool
    ) -> bool:
        """Consume a token once; Later retains the committed cooldown."""
        table = preference_reminders
        async with self._database.transaction() as connection:
            result = await connection.execute(
                table.update()
                .where(
                    table.c.user_id == reservation.user_id,
                    table.c.token == reservation.token,
                )
                .values(dismissed=never, token=None)
                .returning(table.c.user_id)
            )
            accepted = result.scalar_one_or_none() is not None
        return accepted


async def _read_preferences(
    connection: AsyncConnection, table: Table[PreferencesColumns], subject_id: int
) -> Preferences:
    row = (
        await connection.execute(
            select(table.c.language, table.c.timezone, table.c.revision).where(
                table.c.subject_id == subject_id
            )
        )
    ).one_or_none()
    if row is None:
        return Preferences()
    language, timezone, revision = row
    return Preferences(
        None if language is None else Language(language), timezone, revision
    )


async def _complete_setup(connection: AsyncConnection, user_id: int) -> None:
    """Stop onboarding reminders after an explicit personal settings save."""
    await ensure_user(connection, user_id)
    table = preference_reminders
    statement = insert(table).values(
        user_id=user_id,
        eligible_uses=0,
        prompt_count=0,
        last_prompt_us=None,
        dismissed=True,
        token=None,
    )
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.user_id],
            set_={"dismissed": True, "token": None},
            where=table.c.dismissed.is_(False),
        )
    )
