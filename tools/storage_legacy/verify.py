"""Verify imported feature values and exact voice replay before publication."""

from collections import Counter
from datetime import UTC, timedelta

from sqlalchemy import String, select, text

from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.model import VoiceJournalRecord, VoiceObservation, VoiceSnapshot
from api.voice.profile.build import build_profile
from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.scope import VoiceScope
from api.voice.timeline import VoiceTimeline, build_timeline
from repositories.birthday_repository import BirthdayRepository
from repositories.blocking_repository import BlockingRepository
from repositories.monitor_repository import MonitorRepository
from repositories.sqlite.database import SCHEMA_REVISION, Database
from repositories.sqlite.schema import (
    question_answers,
    report_settings,
    reports,
    runtime_checkpoint,
)
from repositories.voice_repository import VoiceRepository
from repositories.volume_repository import VolumeRepository
from tools.storage_legacy.sources import LegacyData
from utils.asyncio_utils import run_in_thread


async def verify_import(database: Database, data: LegacyData) -> None:
    """Compare detached domain values, list ordering and confirmed voice semantics."""
    birthdays = BirthdayRepository(database)
    for expected in data.birthdays:
        actual = await birthdays.get(expected.guild_id)
        if (
            actual != expected
            or actual is None
            or list(actual.users) != list(expected.users)
        ):
            raise ValueError("Birthday migration equivalence failed")
    if sorted(
        await VolumeRepository(database).get_all(), key=lambda item: item.guild_id
    ) != sorted(data.volumes, key=lambda item: item.guild_id):
        raise ValueError("Volume migration equivalence failed")
    blocks = BlockingRepository(database)
    for gid, expected in data.blocks:
        if await blocks.get((gid, expected.user_id)) != expected:
            raise ValueError("Block state/audit migration equivalence failed")
    await _verify_monitor(database, data)
    await _verify_misc(database, data)
    await _verify_voice(database, data)
    await _verify_constraints(database)


async def _verify_constraints(database: Database) -> None:
    async with database.transaction() as connection:
        if (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all():
            raise ValueError("Imported database has invalid foreign keys")
        if (await connection.exec_driver_sql("PRAGMA integrity_check")).all() != [
            ("ok",)
        ]:
            raise ValueError("Imported database failed integrity check")
        revisions = await connection.scalars(
            text("SELECT version_num FROM alembic_version").columns(version_num=String)
        )
        if list(revisions) != [SCHEMA_REVISION]:
            raise ValueError("Imported database has an unexpected schema")


async def _verify_monitor(database: Database, data: LegacyData) -> None:
    monitor = MonitorRepository(database)
    for expected_monitor in data.monitors:
        if (
            await monitor.settings(expected_monitor.guild_id)
            != expected_monitor.settings
        ):
            raise ValueError("Monitor settings migration equivalence failed")
        actual_snapshots = await monitor.snapshots(expected_monitor.guild_id)
        actual_members = {
            item.user_id: (item.username, item.roles, item.left_at)
            for item in actual_snapshots
        }
        expected_members = {
            item.user_id: (item.username, item.roles, item.left_at)
            for item in expected_monitor.snapshots
        }
        if actual_members != expected_members:
            raise ValueError("Role snapshot migration equivalence failed")


async def _verify_misc(database: Database, data: LegacyData) -> None:
    async with database.transaction() as connection:
        questions = list(
            (
                await connection.execute(
                    select(
                        question_answers.c.user_id,
                        question_answers.c.normalized_question,
                        question_answers.c.answer,
                    )
                )
            ).all()
        )
        if sorted(questions) != sorted(data.questions):
            raise ValueError("Question migration equivalence failed")
        channel = await connection.scalar(
            select(report_settings.c.channel_id).where(report_settings.c.singleton == 1)
        )
        if channel != data.report_channel:
            raise ValueError("Report channel migration equivalence failed")
        rows = await connection.execute(
            select(
                reports.c.report_id,
                reports.c.user_id,
                reports.c.guild_id,
                reports.c.channel_id,
                reports.c.reason,
                reports.c.created_text,
                reports.c.user_name_at_event,
                reports.c.avatar_at_event,
                reports.c.guild_name_at_event,
                reports.c.channel_name_at_event,
            ).order_by(reports.c.position)
        )
        actual_reports = [
            {
                "report_id": rid,
                "reason": reason,
                "created_at": created,
                "user": {"id": uid, "name": name, "avatar": avatar},
                "guild": {"id": gid, "name": guild},
                "channel": {"id": cid, "name": channel_name},
            }
            for (
                rid,
                uid,
                gid,
                cid,
                reason,
                created,
                name,
                avatar,
                guild,
                channel_name,
            ) in rows
        ]
        if actual_reports != data.reports:
            raise ValueError("Report snapshot/order migration equivalence failed")
        checkpoint = (
            await connection.execute(
                select(
                    runtime_checkpoint.c.checkpoint_us,
                    runtime_checkpoint.c.accumulated_us,
                )
            )
        ).one_or_none()
        if (
            None if checkpoint is None else (checkpoint[0], checkpoint[1])
        ) != data.uptime:
            raise ValueError("Uptime migration equivalence failed")


async def _verify_voice(database: Database, data: LegacyData) -> None:
    store = VoiceRepository(database)
    scopes = {record.guild_id for record in data.voice}
    restored: list[VoiceJournalRecord] = []
    for scope in scopes:
        records = await store.read_all(scope)
        expected = tuple(record for record in data.voice if record.guild_id == scope)
        if records != expected:
            raise ValueError("Voice raw facts/order migration equivalence failed")
        restored.extend(records)

    await run_in_thread(lambda: _compare_voice(data, restored))


def _compare_voice(data: LegacyData, restored: list[VoiceJournalRecord]) -> None:
    before, after = build_timeline(data.voice), build_timeline(restored)
    if before != after or Counter(data.voice) != Counter(restored):
        raise ValueError("Voice timeline, coverage or gaps differ after import")
    contexts = _voice_contexts(data)
    policy = VoiceXpPolicy()
    for guild_id, user_id in contexts:
        scope = VoiceScope(guild_id=guild_id)
        if policy.calculate(before, user_id, scope) != policy.calculate(
            after, user_id, scope
        ):
            raise ValueError("Exact voice XP differs after import")

    for user_id in {uid for _, uid in contexts}:
        if policy.calculate(before, user_id) != policy.calculate(after, user_id):
            raise ValueError("Exact global voice XP differs after import")
    _compare_read_models(data, before, after, contexts)


def _compare_read_models(
    data: LegacyData,
    before: VoiceTimeline,
    after: VoiceTimeline,
    contexts: set[tuple[int, int]],
) -> None:
    if not data.voice:
        return
    # Fixed to the source horizon, never the verification machine's current date.
    as_of = max(record.observed_at for record in data.voice) + timedelta(microseconds=1)
    for guild_id, user_id in contexts:
        if build_profile(before, user_id, guild_id, "UTC") != build_profile(
            after, user_id, guild_id, "UTC"
        ):
            raise ValueError("Voice profile differs after import")
        for builder in (build_activity_detail, build_people_detail, build_xp_detail):
            if builder(before, user_id, guild_id, as_of, UTC) != builder(
                after, user_id, guild_id, as_of, UTC
            ):
                raise ValueError("Voice detail differs after import")


def _voice_contexts(data: LegacyData) -> set[tuple[int, int]]:
    contexts: set[tuple[int, int]] = set()
    for record in data.voice:
        if record.guild_id is None:
            continue
        if isinstance(record.fact, VoiceObservation):
            contexts.add((record.guild_id, record.fact.state.user_id))
        elif isinstance(record.fact, VoiceSnapshot):
            contexts.update(
                (record.guild_id, state.user_id) for state in record.fact.states
            )
    return contexts
