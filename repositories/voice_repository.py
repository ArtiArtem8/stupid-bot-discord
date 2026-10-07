"""Transactional normalized voice facts, batch identity and committed read revisions."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import NamedTuple, TypedDict

from sqlalchemy import func, select
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


class _StoredRecord(NamedTuple):
    """Typed detached event fields in projection order."""

    record_id: int
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


class _StoredState(NamedTuple):
    """Detached member state belonging to one ordered event."""

    record_id: int
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


_RECORD_FIELDS = select(
    voice_records.c.record_id,
    voice_records.c.boot_id,
    voice_records.c.sequence,
    voice_records.c.observed_us,
    voice_records.c.monotonic,
    voice_records.c.kind,
    voice_records.c.guild_id,
    voice_records.c.authoritative,
    voice_records.c.stopped,
    voice_records.c.gap_start_us,
    voice_records.c.gap_end_us,
    voice_records.c.gap_reason,
    voice_records.c.known_bounds,
)
_STATE_FIELDS = select(
    voice_record_states.c.record_id,
    voice_record_states.c.user_id,
    voice_record_states.c.channel_id,
    voice_record_states.c.channel_known,
    voice_record_states.c.is_bot,
    voice_record_states.c.self_mute,
    voice_record_states.c.self_deaf,
    voice_record_states.c.server_mute,
    voice_record_states.c.server_deaf,
    voice_record_states.c.self_stream,
    voice_record_states.c.self_video,
    voice_record_states.c.suppress,
    voice_record_states.c.afk,
    voice_record_states.c.requested_to_speak,
    voice_record_states.c.requested_us,
    voice_record_states.c.session_id,
)


@dataclass(frozen=True, slots=True)
class VoiceCommit:
    """Identity of a confirmed batch; record IDs need not be contiguous."""

    revision: int
    last_record_id: int


@dataclass(frozen=True, slots=True)
class VoiceHistory:
    """Detached full history or tail with metadata from the same SQL snapshot."""

    records: tuple[VoiceJournalRecord, ...]
    cutoff: VoiceCommit
    shared_revision: int
    guild_revisions: dict[int, int]


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


class VoiceRepository:
    """Own atomic batches and consistent history, releasing SQL before replay.

    A caller chooses a stable batch ID before attempting persistence. Repeating
    its exact ordered content resolves an ambiguous commit; differing reuse fails.
    Equal boot/sequence facts are distinct records, including loss markers.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, batch_id: str, records: Sequence[VoiceJournalRecord]) -> int:
        """Append offline facts, returning their idempotent batch revision."""
        return (await self.append_batch(batch_id, records)).revision

    async def append_batch(
        self, batch_id: str, records: Sequence[VoiceJournalRecord]
    ) -> VoiceCommit:
        """Commit a batch and return its actual final record ID and revision."""
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
                last_id = await connection.scalar(
                    select(func.max(voice_records.c.record_id)).where(
                        voice_records.c.batch_id == batch_id
                    )
                )
                if last_id is None:
                    raise RuntimeError("Persisted voice batch has no records")
                return VoiceCommit(existing[1], last_id)
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
            last_id = 0
            for ordinal, (envelope, states) in enumerate(values):
                last_id = await _insert_record(
                    connection, batch_id, ordinal, envelope, states
                )
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
            return VoiceCommit(revision, last_id)

    async def history(self, *, after_record_id: int = 0) -> VoiceHistory:
        """Read all scopes after a real ID, with a transaction-consistent cutoff.

        Runtime writes pass through the analytics owner. This API also supports
        offline replay; decoding never retains a SQL connection.
        """
        async with self._database.transaction() as connection:
            global_revision, shared_revision = (
                await connection.execute(
                    select(
                        voice_shared_revision.c.global_revision,
                        voice_shared_revision.c.revision,
                    ).where(voice_shared_revision.c.singleton == 1)
                )
            ).one()
            revision_rows = await connection.execute(
                select(voice_revisions.c.guild_id, voice_revisions.c.revision)
            )
            guild_revisions = dict(iter(revision_rows))
            last_id = (
                await connection.scalar(select(func.max(voice_records.c.record_id)))
                or 0
            )
            rows = await connection.execute(
                _RECORD_FIELDS.where(
                    voice_records.c.record_id > after_record_id,
                    voice_records.c.record_id <= last_id,
                ).order_by(voice_records.c.record_id)
            )
            records = [_StoredRecord(*row) for row in rows]
            states = await connection.execute(
                _STATE_FIELDS.where(
                    voice_record_states.c.record_id > after_record_id,
                    voice_record_states.c.record_id <= last_id,
                ).order_by(
                    voice_record_states.c.record_id, voice_record_states.c.position
                )
            )
            raw = records, [_StoredState(*row) for row in states]
        decoded = await run_in_thread(lambda: _decode_records(raw))
        return VoiceHistory(
            decoded,
            VoiceCommit(global_revision, last_id),
            shared_revision,
            guild_revisions,
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
) -> int:
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
    return record_id


async def _read_records(
    connection: AsyncConnection,
    guild_id: int | None,
    *,
    day: date | None = None,
) -> tuple[list[_StoredRecord], list[_StoredState]]:
    events = _RECORD_FIELDS
    if day is not None:
        start = utc_microseconds(datetime.combine(day, time(), UTC))
        end = utc_microseconds(datetime.combine(day + timedelta(days=1), time(), UTC))
        events = events.where(
            voice_records.c.observed_us >= start, voice_records.c.observed_us < end
        )
    scoped = events.where(voice_records.c.guild_id == guild_id)
    query = scoped.order_by(voice_records.c.record_id)
    rows = await connection.execute(query)
    records = [_StoredRecord(*row) for row in rows]
    if guild_id is None:
        return records, []
    states = _STATE_FIELDS.where(voice_record_states.c.guild_id == guild_id)
    if day is not None:
        states = states.where(
            voice_record_states.c.record_id.in_(
                scoped.with_only_columns(voice_records.c.record_id)
            )
        )
    # Composite foreign keys already guarantee the state/event guild agrees.
    state_rows = await connection.execute(
        states.order_by(voice_record_states.c.record_id, voice_record_states.c.position)
    )
    return records, [_StoredState(*row) for row in state_rows]


def _decode_state(state: _StoredState) -> VoiceStateSnapshot:
    requested = state.requested_us
    return VoiceStateSnapshot(
        user_id=state.user_id,
        channel_id=state.channel_id,
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
        requested_to_speak_at=from_microseconds(requested)
        if requested is not None
        else None,
        session_id=state.session_id,
    )


def _decode_snapshot(
    value: _StoredRecord, states: list[VoiceStateSnapshot]
) -> VoiceSnapshot:
    authoritative = value.authoritative
    if authoritative is None:
        raise ValueError("Snapshot lacks authority")
    return VoiceSnapshot(tuple(states), authoritative)


def _decode_fact(value: _StoredRecord, states: list[VoiceStateSnapshot]) -> VoiceFact:
    match value.kind:
        case "observation":
            if len(states) != 1:
                raise ValueError("Voice observation needs exactly one state")
            return VoiceObservation(states[0])
        case "snapshot":
            return _decode_snapshot(value, states)
        case "checkpoint":
            return VoiceCheckpoint()
        case "lifecycle":
            stopped = value.stopped
            if stopped is None:
                raise ValueError("Lifecycle lacks boundary type")
            return VoiceLifecycle(stopped)
        case "gap":
            return _decode_gap(value)
        case _:
            raise ValueError("Unknown voice fact kind")


def _decode_records(
    raw: tuple[list[_StoredRecord], list[_StoredState]],
) -> tuple[VoiceJournalRecord, ...]:
    records, state_rows = raw
    states: dict[int, list[VoiceStateSnapshot]] = {}
    for state in state_rows:
        states.setdefault(state.record_id, []).append(_decode_state(state))
    return tuple(
        VoiceJournalRecord(
            value.sequence,
            value.boot_id,
            from_microseconds(value.observed_us),
            value.monotonic,
            _decode_fact(value, states.get(value.record_id, [])),
            value.guild_id,
        )
        for value in records
    )


def _decode_gap(value: _StoredRecord) -> ObservationGap:
    start, end, reason, bounds = (
        value.gap_start_us,
        value.gap_end_us,
        value.gap_reason,
        value.known_bounds,
    )
    if start is None or reason is None or bounds is None:
        raise ValueError("Incomplete gap metadata")
    return ObservationGap(
        from_microseconds(start),
        from_microseconds(end) if end is not None else None,
        GapReason(reason),
        value.guild_id,
        bounds,
    )
