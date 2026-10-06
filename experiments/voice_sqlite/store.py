"""Store unmodified voice facts in an isolated, disposable SQLite experiment."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    TypedColumns,
    event,
    select,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import ConnectionPoolEntry

from api.voice.model import VoiceJournalRecord
from repositories.sqlite.database import open_engine
from tools.storage_legacy.voice_codec import decode_record, encode_record

metadata = MetaData()


class BatchColumns(TypedColumns):
    token = Column(String, primary_key=True)
    digest = Column(String, nullable=False)


class FactColumns(TypedColumns):
    ordinal = Column(Integer, primary_key=True)
    batch = Column(String, ForeignKey("trial_batches.token"), nullable=False)
    guild_id: Column[int | None] = Column(nullable=True)
    payload = Column(String, nullable=False)


batches = Table("trial_batches", metadata, BatchColumns)
facts = Table("trial_facts", metadata, FactColumns)
Index("trial_scope_order", facts.c.guild_id, facts.c.ordinal)


def _query_only(connection: DBAPIConnection, _entry: ConnectionPoolEntry) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA query_only=ON")
    finally:
        cursor.close()


def open_reader(path: Path) -> AsyncEngine:
    """Create one bounded query-only connection, independent of the sole writer."""
    engine = open_engine(path)
    event.listen(engine.sync_engine, "connect", _query_only)
    return engine


@dataclass(frozen=True)
class WriteSample:
    """Milliseconds waiting for checkout and exiting the commit context."""

    inserted: bool
    wait_ms: float
    commit_ms: float


@dataclass(frozen=True)
class ReadSample:
    """Detached decoded facts, committed watermark and connection wait in ms."""

    records: tuple[VoiceJournalRecord, ...]
    revision: int
    wait_ms: float


def _encode(records: Sequence[VoiceJournalRecord]) -> tuple[list[str], str]:
    # A float field also accepts integer inputs. Decode returns float, so make
    # retries before and after a restart use the same numeric wire spelling.
    payloads = [
        encode_record(replace(record, monotonic=float(record.monotonic)))
        for record in records
    ]
    return payloads, sha256("\n".join(payloads).encode("utf-8")).hexdigest()


class TrialVoiceStore:
    """Use the birthday pilot's single-owner engine for whole batch transactions.

    The caller supplies a stable operation token before attempting a batch. Retry
    with the same token and same ordered content is a no-op; conflicting reuse is
    an error. Facts, including same-sequence loss markers, are never deduplicated.
    An ambiguous commit is resolved by retrying its token after restart. This is
    an experiment, not a replacement for collector admission and gap policies.
    """

    def __init__(self, engine: AsyncEngine, reader: AsyncEngine | None = None) -> None:
        self.engine = engine
        self.reader = engine if reader is None else reader

    async def initialize(self) -> None:
        """Create disposable trial tables; not a production migration contract."""
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def append(
        self, token: str, records: Sequence[VoiceJournalRecord]
    ) -> WriteSample:
        """Commit one whole batch, retaining input order and every original fact."""
        if not token or not records:
            raise ValueError("A trial batch requires a token and records")
        payloads, digest = await asyncio.to_thread(_encode, records)
        started = perf_counter()
        async with self.engine.connect() as connection:
            wait_ms = (perf_counter() - started) * 1000
            async with connection.begin():
                result = await connection.scalar(
                    insert(batches)
                    .values(token=token, digest=digest)
                    .on_conflict_do_nothing()
                    .returning(batches.c.token)
                )
                inserted = result is not None
                if inserted:
                    rows = [
                        {
                            "batch": token,
                            "guild_id": record.guild_id,
                            "payload": payload,
                        }
                        for record, payload in zip(records, payloads, strict=True)
                    ]
                    await connection.execute(facts.insert(), rows)
                else:
                    previous = await connection.scalar(
                        select(batches.c.digest).where(batches.c.token == token)
                    )
                    if previous != digest:
                        raise ValueError(
                            "Trial batch token reused with different content"
                        )
                committing = perf_counter()
            commit_ms = (perf_counter() - committing) * 1000
        return WriteSample(inserted, wait_ms, commit_ms)

    async def snapshot(self, guild_id: int | None = None) -> ReadSample:
        """Read a committed snapshot, releasing its transaction before decoding.

        None requests the global history. A guild requests its facts plus shared
        session records. The revision belongs to the same read transaction.
        Replay, metrics and rendering must happen after this method returns.
        """
        started = perf_counter()
        async with self.reader.connect() as connection:
            wait_ms = (perf_counter() - started) * 1000
            async with connection.begin():
                revision = await connection.scalar(
                    select(facts.c.ordinal).order_by(facts.c.ordinal.desc()).limit(1)
                )
                query = select(facts.c.payload).order_by(facts.c.ordinal)
                if guild_id is not None:
                    query = query.where(
                        (facts.c.guild_id == guild_id) | facts.c.guild_id.is_(None)
                    )
                payloads = tuple((await connection.scalars(query)).all())
        records = await asyncio.to_thread(
            lambda: tuple(decode_record(payload) for payload in payloads)
        )
        return ReadSample(records, revision or 0, wait_ms)
