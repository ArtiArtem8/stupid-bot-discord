"""Transactional normalized voice facts, batch identity and committed read revisions."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceFact,
    VoiceJournalRecord,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from repositories.sqlite.database import Database
from repositories.sqlite.identity import (
    discord_id,
    ensure_channel,
    ensure_guild,
    ensure_member,
    from_microseconds,
    utc_microseconds,
)
from repositories.sqlite.schema import (
    voice_batches,
    voice_record_states,
    voice_records,
    voice_revisions,
    voice_shared_revision,
)
from utils.asyncio_utils import run_in_thread


class RecordValues(TypedDict):
    boot_id: str
    sequence: int
    observed_us: int
    monotonic: float
    kind: str
    guild_id: int | None
    authoritative: bool | None
    stopped: bool | None
    gap_start_us: int | None
    gap_end_us: int | None
    gap_reason: str | None
    known_bounds: bool | None


class StateValues(TypedDict):
    user_id: int
    channel_id: int | None
    channel_known: bool
    is_bot: bool | None
    self_mute: bool | None
    self_deaf: bool | None
    server_mute: bool | None
    server_deaf: bool | None
    self_stream: bool | None
    self_video: bool | None
    suppress: bool | None
    afk: bool | None
    requested_to_speak: bool | None
    requested_us: int | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class VoiceJournalSnapshot:
    """One committed guild/shared revision, independent of admission telemetry."""

    guild_records: tuple[VoiceJournalRecord, ...]
    session_records: tuple[VoiceJournalRecord, ...]
    generation: int


def _state_values(state: VoiceStateSnapshot) -> StateValues:
    return StateValues(
        user_id=discord_id(state.user_id),
        channel_id=discord_id(state.channel_id)
        if state.channel_id is not None
        else None,
        channel_known=state.channel_known,
        is_bot=state.is_bot,
        self_mute=state.self_mute,
        self_deaf=state.self_deaf,
        server_mute=state.server_mute,
        server_deaf=state.server_deaf,
        self_stream=state.self_stream,
        self_video=state.self_video,
        suppress=state.suppress,
        afk=state.afk,
        requested_to_speak=state.requested_to_speak,
        requested_us=utc_microseconds(state.requested_to_speak_at)
        if state.requested_to_speak_at is not None
        else None,
        session_id=state.session_id,
    )


def _record_values(
    record: VoiceJournalRecord,
) -> tuple[RecordValues, list[StateValues]]:
    values = RecordValues(
        boot_id=record.boot_id,
        sequence=record.sequence,
        observed_us=utc_microseconds(record.observed_at),
        monotonic=float(record.monotonic),
        guild_id=discord_id(record.guild_id) if record.guild_id is not None else None,
        kind="",
        authoritative=None,
        stopped=None,
        gap_start_us=None,
        gap_end_us=None,
        gap_reason=None,
        known_bounds=None,
    )
    states: list[StateValues] = []
    match record.fact:
        case VoiceObservation(state):
            values["kind"] = "observation"
            states.append(_state_values(state))
        case VoiceSnapshot(members, authoritative):
            values["kind"] = "snapshot"
            values["authoritative"] = authoritative
            states = [_state_values(state) for state in members]
        case ObservationGap(start, end, reason, _, known_bounds):
            values["kind"] = "gap"
            values["gap_start_us"] = utc_microseconds(start)
            values["gap_end_us"] = utc_microseconds(end) if end is not None else None
            values["gap_reason"] = reason.value
            values["known_bounds"] = known_bounds
        case VoiceCheckpoint():
            values["kind"] = "checkpoint"
        case VoiceLifecycle(stopped):
            values["kind"] = "lifecycle"
            values["stopped"] = stopped
    return values, states


def _batch_values(
    records: Sequence[VoiceJournalRecord],
) -> tuple[list[tuple[RecordValues, list[StateValues]]], str]:
    values = [_record_values(record) for record in records]
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return values, sha256(encoded.encode("utf-8")).hexdigest()


async def _revision(connection: AsyncConnection, guild_id: int) -> int:
    shared = await connection.scalar(
        select(voice_shared_revision.c.revision).where(
            voice_shared_revision.c.singleton == 1
        )
    )
    scoped = await connection.scalar(
        select(voice_revisions.c.revision).where(voice_revisions.c.guild_id == guild_id)
    )
    if shared is None:
        raise RuntimeError("Missing voice revision state")
    return max(shared, scoped or 0)


class VoiceStore:
    """Own atomic batches and scoped snapshots, releasing SQL before replay.

    A caller chooses a stable batch ID before attempting persistence. Repeating
    its exact ordered content resolves an ambiguous commit; differing reuse fails.
    Equal boot/sequence facts are distinct records, including loss markers.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, batch_id: str, records: Sequence[VoiceJournalRecord]) -> int:
        if not batch_id or not records:
            raise ValueError("A voice batch requires identity and content")
        values, fingerprint = await run_in_thread(lambda: _batch_values(records))
        async with self._database.transaction() as connection:
            existing = (
                await connection.execute(
                    select(voice_batches.c.fingerprint, voice_batches.c.revision).where(
                        voice_batches.c.batch_id == batch_id
                    )
                )
            ).one_or_none()
            if existing is not None:
                if existing[0] != fingerprint:
                    raise ValueError("Voice batch ID reused with different content")
                return existing[1]
            revision = (
                await connection.execute(
                    voice_shared_revision.update()
                    .where(voice_shared_revision.c.singleton == 1)
                    .values(global_revision=voice_shared_revision.c.global_revision + 1)
                    .returning(voice_shared_revision.c.global_revision)
                )
            ).scalar_one()
            await connection.execute(
                insert(voice_batches).values(
                    batch_id=batch_id,
                    fingerprint=fingerprint,
                    record_count=len(records),
                    revision=revision,
                )
            )
            scopes = {record.guild_id for record in records}
            for ordinal, (envelope, states) in enumerate(values):
                await _insert_record(connection, batch_id, ordinal, envelope, states)
            for scope in scopes:
                if scope is None:
                    await connection.execute(
                        voice_shared_revision.update()
                        .where(voice_shared_revision.c.singleton == 1)
                        .values(revision=revision)
                    )
                else:
                    await connection.execute(
                        insert(voice_revisions)
                        .values(guild_id=scope, revision=revision)
                        .on_conflict_do_update(
                            index_elements=[voice_revisions.c.guild_id],
                            set_={"revision": revision},
                        )
                    )
            return revision

    async def revision(self, guild_id: int) -> int:
        async with self._database.transaction() as connection:
            return await _revision(connection, guild_id)

    async def snapshot_for_guild(self, guild_id: int) -> VoiceJournalSnapshot:
        async with self._database.transaction() as connection:
            revision = await _revision(connection, guild_id)
            raw = await _read_records(connection, guild_id, include_shared=True)
        records = await run_in_thread(lambda: _decode_records(raw))
        return VoiceJournalSnapshot(
            tuple(r for r in records if r.guild_id == guild_id),
            tuple(r for r in records if r.guild_id is None),
            revision,
        )

    async def read_all(
        self, guild_id: int | None, day: date | None = None
    ) -> tuple[VoiceJournalRecord, ...]:
        async with self._database.transaction() as connection:
            raw = await _read_records(connection, guild_id, day=day)
        return await run_in_thread(lambda: _decode_records(raw))


async def _insert_record(
    connection: AsyncConnection,
    batch_id: str,
    ordinal: int,
    envelope: RecordValues,
    states: list[StateValues],
) -> None:
    guild_id = envelope["guild_id"]
    if guild_id is not None:
        await ensure_guild(connection, guild_id)
    record_id = (
        await connection.execute(
            insert(voice_records)
            .values(batch_id=batch_id, ordinal=ordinal, **envelope)
            .returning(voice_records.c.record_id)
        )
    ).scalar_one()
    for position, state in enumerate(states):
        if guild_id is None:
            raise ValueError("Voice states require a guild context")
        await ensure_member(connection, guild_id, state["user_id"])
        if state["channel_id"] is not None:
            await ensure_channel(connection, state["channel_id"], guild_id)
        await connection.execute(
            insert(voice_record_states).values(
                record_id=record_id, position=position, guild_id=guild_id, **state
            )
        )


async def _read_records(
    connection: AsyncConnection,
    guild_id: int | None,
    *,
    include_shared: bool = False,
    day: date | None = None,
) -> list[tuple[RecordValues, list[StateValues]]]:
    scope = (
        voice_records.c.guild_id.is_(None)
        if guild_id is None
        else voice_records.c.guild_id == guild_id
    )
    if include_shared:
        scope = scope | voice_records.c.guild_id.is_(None)
    if day is not None:
        start = utc_microseconds(datetime.combine(day, time(), UTC))
        end = utc_microseconds(datetime.combine(day + timedelta(days=1), time(), UTC))
        scope = (
            scope
            & (voice_records.c.observed_us >= start)
            & (voice_records.c.observed_us < end)
        )
    query = select(voice_records).where(scope).order_by(voice_records.c.record_id)
    rows = await connection.execute(query)
    records: dict[int, tuple[RecordValues, list[StateValues]]] = {}
    for (
        rid,
        _batch,
        _ordinal,
        boot,
        seq,
        observed,
        mono,
        kind,
        gid,
        authority,
        stopped,
        start_us,
        end_us,
        reason,
        bounds,
    ) in rows:
        records[rid] = (
            RecordValues(
                boot_id=boot,
                sequence=seq,
                observed_us=observed,
                monotonic=mono,
                kind=kind,
                guild_id=gid,
                authoritative=authority,
                stopped=stopped,
                gap_start_us=start_us,
                gap_end_us=end_us,
                gap_reason=reason,
                known_bounds=bounds,
            ),
            [],
        )
    states = await connection.execute(
        select(voice_record_states)
        .join(
            voice_records, voice_records.c.record_id == voice_record_states.c.record_id
        )
        .where(scope)
        .order_by(voice_record_states.c.record_id, voice_record_states.c.position)
    )
    for (
        rid,
        _position,
        _guild_id,
        uid,
        channel,
        known,
        bot,
        mute,
        deaf,
        server_mute,
        server_deaf,
        stream,
        video,
        suppress,
        afk,
        requested,
        requested_us,
        session,
    ) in states:
        records[rid][1].append(
            StateValues(
                user_id=uid,
                channel_id=channel,
                channel_known=known,
                is_bot=bot,
                self_mute=mute,
                self_deaf=deaf,
                server_mute=server_mute,
                server_deaf=server_deaf,
                self_stream=stream,
                self_video=video,
                suppress=suppress,
                afk=afk,
                requested_to_speak=requested,
                requested_us=requested_us,
                session_id=session,
            )
        )
    return list(records.values())


def _decode_state(state: StateValues) -> VoiceStateSnapshot:
    requested = state["requested_us"]
    return VoiceStateSnapshot(
        user_id=state["user_id"],
        channel_id=state["channel_id"],
        channel_known=state["channel_known"],
        is_bot=state["is_bot"],
        self_mute=state["self_mute"],
        self_deaf=state["self_deaf"],
        server_mute=state["server_mute"],
        server_deaf=state["server_deaf"],
        self_stream=state["self_stream"],
        self_video=state["self_video"],
        suppress=state["suppress"],
        afk=state["afk"],
        requested_to_speak=state["requested_to_speak"],
        requested_to_speak_at=from_microseconds(requested)
        if requested is not None
        else None,
        session_id=state["session_id"],
    )


def _decode_snapshot(value: RecordValues, states: list[StateValues]) -> VoiceSnapshot:
    authoritative = value["authoritative"]
    if authoritative is None:
        raise ValueError("Snapshot lacks authority")
    return VoiceSnapshot(tuple(_decode_state(state) for state in states), authoritative)


def _decode_fact(value: RecordValues, states: list[StateValues]) -> VoiceFact:
    match value["kind"]:
        case "observation":
            if len(states) != 1:
                raise ValueError("Voice observation needs exactly one state")
            return VoiceObservation(_decode_state(states[0]))
        case "snapshot":
            return _decode_snapshot(value, states)
        case "checkpoint":
            return VoiceCheckpoint()
        case "lifecycle":
            stopped = value["stopped"]
            if stopped is None:
                raise ValueError("Lifecycle lacks boundary type")
            return VoiceLifecycle(stopped)
        case "gap":
            return _decode_gap(value)
        case _:
            raise ValueError("Unknown voice fact kind")


def _decode_records(
    raw: list[tuple[RecordValues, list[StateValues]]],
) -> tuple[VoiceJournalRecord, ...]:
    return tuple(
        VoiceJournalRecord(
            value["sequence"],
            value["boot_id"],
            from_microseconds(value["observed_us"]),
            value["monotonic"],
            _decode_fact(value, states),
            value["guild_id"],
        )
        for value, states in raw
    )


def _decode_gap(value: RecordValues) -> ObservationGap:
    start, end, reason, bounds = (
        value["gap_start_us"],
        value["gap_end_us"],
        value["gap_reason"],
        value["known_bounds"],
    )
    if start is None or reason is None or bounds is None:
        raise ValueError("Incomplete gap metadata")
    return ObservationGap(
        from_microseconds(start),
        from_microseconds(end) if end is not None else None,
        GapReason(reason),
        value["guild_id"],
        bounds,
    )
