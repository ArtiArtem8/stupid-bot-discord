"""Import validated legacy values into an unpublished application database."""

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from api.report_models import ReportDataDict
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    ensure_channel,
    ensure_guild,
    ensure_member,
    ensure_role,
    ensure_user,
    utc_microseconds,
)
from repositories.sqlite.schema import (
    birthday_history,
    birthday_settings,
    block_events,
    member_birthdays,
    member_blocks,
    member_name_observations,
    monitor_settings,
    music_settings,
    question_answers,
    report_settings,
    reports,
    role_snapshot_roles,
    role_snapshots,
    runtime_checkpoint,
    uptime_periods,
)
from tools.storage_legacy.sources import LegacyData


def birth_date(value: str) -> str | None:
    """Preserve empty dates; reject malformed nonempty calendar values."""
    if not value:
        return None
    if len(value) != 10:
        raise ValueError("Resolve invalid birthday before publishing the database")
    day, month, year = map(int, value.split("-"))
    return date(year, month, day).isoformat()


def report_time(value: str, timezone: ZoneInfo | None) -> tuple[int | None, str]:
    """Retain unknown/DST-ambiguous legacy instants instead of guessing an offset."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parts = time.strptime(value, "%d.%m.%Y %H:%M:%S")
        parsed = datetime(
            parts.tm_year,
            parts.tm_mon,
            parts.tm_mday,
            parts.tm_hour,
            parts.tm_min,
            parts.tm_sec,
            tzinfo=timezone,
        )
        parsed = parsed.replace(tzinfo=None)
    if parsed.utcoffset() is not None:
        return utc_microseconds(
            parsed
        ), "legacy_explicit_zone" if timezone else "legacy_aware"
    if timezone is None:
        return None, "legacy_timezone_unknown"
    # A local wall time is resolvable only when both folds agree and round-trip.
    first = parsed.replace(tzinfo=timezone, fold=0)
    second = parsed.replace(tzinfo=timezone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None, "legacy_timezone_ambiguous"
    if (
        datetime.fromtimestamp(first.timestamp(), timezone).replace(tzinfo=None)
        != parsed
    ):
        return None, "legacy_timezone_nonexistent"
    return utc_microseconds(first), "legacy_explicit_zone"


async def import_features(
    database: Database, data: LegacyData, timezone: ZoneInfo | None
) -> None:
    """Import the finite non-voice snapshot in one rollback-capable transaction."""
    async with database.transaction() as connection:
        await _birthdays(connection, data)
        await _blocks(connection, data)
        await _reports(connection, data, timezone)
        await _monitoring(connection, data)
        for volume in data.volumes:
            await ensure_guild(connection, volume.guild_id)
            await connection.execute(
                insert(music_settings).values(
                    guild_id=volume.guild_id, volume=volume.volume, version=1
                )
            )
        for uid, question, answer in data.questions:
            await ensure_user(connection, uid)
            await connection.execute(
                insert(question_answers).values(
                    user_id=uid, normalized_question=question, answer=answer
                )
            )
        if data.uptime is not None:
            checkpoint, accumulated = data.uptime
            period = (
                await connection.execute(
                    insert(uptime_periods)
                    .values(
                        started_us=None,
                        accumulated_us=accumulated,
                        last_checkpoint_us=checkpoint,
                        origin="legacy_checkpoint",
                    )
                    .returning(uptime_periods.c.period_id)
                )
            ).scalar_one()
            await connection.execute(
                insert(runtime_checkpoint).values(
                    singleton=1,
                    checkpoint_us=checkpoint,
                    accumulated_us=accumulated,
                    origin="legacy_checkpoint",
                    boot_id=None,
                    period_id=period,
                )
            )


async def _birthdays(connection: AsyncConnection, data: LegacyData) -> None:
    for guild in data.birthdays:
        await ensure_guild(connection, guild.guild_id)
        if guild.channel_id is not None:
            await ensure_channel(connection, guild.channel_id, guild.guild_id)
        if guild.birthday_role_id is not None:
            await ensure_role(connection, guild.birthday_role_id, guild.guild_id)
        await connection.execute(
            insert(birthday_settings).values(
                guild_id=guild.guild_id,
                channel_id=guild.channel_id,
                birthday_role_id=guild.birthday_role_id,
                guild_name_hint=guild.server_name,
                version=1,
            )
        )
        for position, user in enumerate(guild.users.values()):
            await ensure_member(connection, guild.guild_id, user.user_id)
            await connection.execute(
                insert(member_birthdays).values(
                    guild_id=guild.guild_id,
                    user_id=user.user_id,
                    birth_date=birth_date(user.birthday),
                    display_name_hint=user.name,
                    position=position,
                    version=1,
                )
            )
            for ordinal, value in enumerate(user.was_congrats):
                await connection.execute(
                    insert(birthday_history).values(
                        guild_id=guild.guild_id,
                        user_id=user.user_id,
                        position=ordinal,
                        value=value,
                        origin="legacy",
                    )
                )


async def _blocks(connection: AsyncConnection, data: LegacyData) -> None:
    for gid, user in data.blocks:
        await ensure_member(connection, gid, user.user_id)
        await connection.execute(
            insert(member_blocks).values(
                guild_id=gid,
                user_id=user.user_id,
                blocked=user.blocked,
                display_name_hint=user.current_username,
                username_hint=user.current_global_name,
                version=1,
            )
        )
        for action, entries in (
            ("block", user.block_history),
            ("unblock", user.unblock_history),
        ):
            for ordinal, entry in enumerate(entries):
                await ensure_user(connection, entry.admin_id)
                await connection.execute(
                    insert(block_events).values(
                        guild_id=gid,
                        user_id=user.user_id,
                        action=action,
                        ordinal=ordinal,
                        admin_id=entry.admin_id,
                        reason=entry.reason,
                        created_us=utc_microseconds(entry.timestamp),
                        origin="legacy",
                    )
                )
        for ordinal, name in enumerate(user.name_history):
            await connection.execute(
                insert(member_name_observations).values(
                    guild_id=gid,
                    user_id=user.user_id,
                    ordinal=ordinal,
                    display_name=name.username,
                    created_us=utc_microseconds(name.timestamp),
                )
            )


async def _reports(
    connection: AsyncConnection, data: LegacyData, timezone: ZoneInfo | None
) -> None:
    if data.report_channel is not None:
        await ensure_channel(connection, data.report_channel, None)
        await connection.execute(
            insert(report_settings).values(
                singleton=1, channel_id=data.report_channel, version=1
            )
        )
    for position, report in enumerate(data.reports):
        await _report(connection, report, timezone, position)


async def _report(
    connection: AsyncConnection,
    report: ReportDataDict,
    timezone: ZoneInfo | None,
    position: int,
) -> None:
    uid, gid, cid = report["user"]["id"], report["guild"]["id"], report["channel"]["id"]
    await ensure_user(connection, uid)
    if gid is not None:
        await ensure_member(connection, gid, uid)
    if cid is not None:
        await ensure_channel(connection, cid, gid)
    instant, provenance = report_time(report["created_at"], timezone)
    await connection.execute(
        insert(reports).values(
            position=position,
            report_id=report["report_id"],
            request_key=None,
            user_id=uid,
            guild_id=gid,
            channel_id=cid,
            reason=report["reason"],
            created_us=instant,
            created_text=report["created_at"],
            provenance=provenance,
            user_name_at_event=report["user"]["name"],
            avatar_at_event=report["user"]["avatar"],
            guild_name_at_event=report["guild"]["name"],
            channel_name_at_event=report["channel"]["name"],
        )
    )


async def _monitoring(connection: AsyncConnection, data: LegacyData) -> None:
    for monitor in data.monitors:
        gid = monitor.guild_id
        await ensure_guild(connection, gid)
        await connection.execute(
            insert(monitor_settings).values(
                guild_id=gid,
                enabled=monitor.settings.enabled,
                ttl_days=monitor.settings.ttl_days,
                version=1,
            )
        )
        for snapshot in monitor.snapshots:
            await ensure_member(connection, gid, snapshot.user_id)
            sid = (
                await connection.execute(
                    insert(role_snapshots)
                    .values(
                        guild_id=gid,
                        user_id=snapshot.user_id,
                        username_at_leave=snapshot.username,
                        left_us=utc_microseconds(snapshot.left_at),
                        version=1,
                    )
                    .returning(role_snapshots.c.snapshot_id)
                )
            ).scalar_one()
            for position, role in enumerate(snapshot.roles):
                await ensure_role(connection, role, gid)
                await connection.execute(
                    insert(role_snapshot_roles).values(
                        snapshot_id=sid, guild_id=gid, role_id=role, position=position
                    )
                )
