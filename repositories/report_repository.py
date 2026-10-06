"""Persist reports before feedback, with independent best-effort notifications."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from api.report_models import ReportDataDict
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    ensure_channel,
    ensure_member,
    ensure_user,
    observe_guild_name,
    observe_username,
    utc_microseconds,
)
from repositories.sqlite.rows import column_value
from repositories.sqlite.schema import report_settings, reports


async def _existing(
    connection: AsyncConnection, request_key: str
) -> ReportDataDict | None:
    columns = reports.c
    query = select(
        columns.report_id,
        columns.user_id,
        columns.guild_id,
        columns.channel_id,
        columns.reason,
        columns.created_text,
        columns.user_name_at_event,
        columns.avatar_at_event,
        columns.guild_name_at_event,
        columns.channel_name_at_event,
    ).where(columns.request_key == request_key)
    result = await connection.execute(query)
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {
        "report_id": column_value(row, columns.report_id),
        "reason": column_value(row, columns.reason),
        "created_at": column_value(row, columns.created_text) or "",
        "user": {
            "id": column_value(row, columns.user_id),
            "name": column_value(row, columns.user_name_at_event),
            "avatar": column_value(row, columns.avatar_at_event),
        },
        "guild": {
            "id": column_value(row, columns.guild_id),
            "name": column_value(row, columns.guild_name_at_event),
        },
        "channel": {
            "id": column_value(row, columns.channel_id),
            "name": column_value(row, columns.channel_name_at_event),
        },
    }


class ReportRepository:
    """Own developer-channel settings and immutable report insertion.

    A repeated interaction key returns its original report and suppresses another
    notification attempt. A conflicting logical payload raises before mutation.
    Channel notifications are never sent inside the database transaction.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def set_channel(self, channel_id: int) -> None:
        """Change the global developer destination independently of report history."""
        async with self._database.transaction() as connection:
            await ensure_channel(connection, channel_id, None)
            statement = insert(report_settings).values(
                singleton=1, channel_id=channel_id, version=1
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[report_settings.c.singleton],
                    set_={
                        "channel_id": channel_id,
                        "version": report_settings.c.version + 1,
                    },
                    where=report_settings.c.channel_id.is_distinct_from(channel_id),
                )
            )

    async def submit(
        self, report: ReportDataDict, request_key: str
    ) -> tuple[ReportDataDict, int | None]:
        """Commit a new report, returning it and the best-effort notification target."""
        created = utc_microseconds(datetime.fromisoformat(report["created_at"]))
        async with self._database.transaction() as connection:
            previous = await _existing(connection, request_key)
            if previous is not None:
                if (
                    previous["user"]["id"],
                    previous["guild"]["id"],
                    previous["channel"]["id"],
                    previous["reason"],
                ) != (
                    report["user"]["id"],
                    report["guild"]["id"],
                    report["channel"]["id"],
                    report["reason"],
                ):
                    raise ValueError("Conflicting report interaction payload")
                return previous, None
            await ensure_user(connection, report["user"]["id"])
            await observe_username(
                connection, report["user"]["id"], report["user"]["name"], created
            )
            guild_id = report["guild"]["id"]
            if guild_id is not None:
                await ensure_member(connection, guild_id, report["user"]["id"])
                guild_name = report["guild"]["name"]
                if guild_name is not None:
                    await observe_guild_name(connection, guild_id, guild_name, created)
            channel_id = report["channel"]["id"]
            if channel_id is not None:
                await ensure_channel(connection, channel_id, guild_id)
            await connection.execute(
                insert(reports).values(
                    position=select(
                        func.coalesce(func.max(reports.c.position), -1) + 1
                    ).scalar_subquery(),
                    report_id=report["report_id"],
                    request_key=request_key,
                    user_id=report["user"]["id"],
                    guild_id=guild_id,
                    channel_id=channel_id,
                    reason=report["reason"],
                    created_us=created,
                    created_text=report["created_at"],
                    user_name_at_event=report["user"]["name"],
                    avatar_at_event=report["user"]["avatar"],
                    guild_name_at_event=report["guild"]["name"],
                    channel_name_at_event=report["channel"]["name"],
                    provenance="observed",
                )
            )
            channel = await connection.scalar(
                select(report_settings.c.channel_id).where(
                    report_settings.c.singleton == 1
                )
            )
            return report, channel
