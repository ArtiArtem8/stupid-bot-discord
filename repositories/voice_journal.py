"""Single-owner bounded voice queue backed by atomic SQLite batches.

Admission remains synchronous. Only committed batches count as persisted;
telemetry never doubles as a read revision. Failed writes open conservative
loss windows, and closing drains accepted work before database disposal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import uuid4

from api.voice.model import GapReason, ObservationGap, VoiceJournalRecord
from repositories.voice_store import VoiceJournalSnapshot, VoiceStore

logger = logging.getLogger(__name__)


class Submission(StrEnum):
    """Buffer admission result, never a durability acknowledgement."""

    ACCEPTED = "accepted"
    FULL = "full"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class JournalCounts:
    """Record counts; persisted excludes failed/partially written batches."""

    received: int
    accepted: int
    persisted: int
    failed: int
    rejected: int


class JournalWriteError(OSError):
    """At least one accepted batch failed; close could not promise persistence."""


class VoiceJournal:
    """Own queue admission, write-failure gaps and writer shutdown."""

    def __init__(
        self, store: VoiceStore, *, queue_size: int = 10_000, batch_size: int = 500
    ) -> None:
        if queue_size < 1 or batch_size < 1:
            raise ValueError("Queue and batch sizes must be positive")
        self.store = store
        self._queue: asyncio.Queue[VoiceJournalRecord | None] = asyncio.Queue(
            queue_size
        )
        self._batch_size = batch_size
        self._writer: asyncio.Task[None] | None = None
        self._closing = False
        self._received = self._accepted = self._persisted = 0
        self._failed = self._rejected = 0
        self._losses: dict[int | None, VoiceJournalRecord] = {}
        self._write_error: Exception | None = None
        self._close_task: asyncio.Task[None] | None = None

    @property
    def closed(self) -> bool:
        """Return whether this queue owner has finished draining."""
        return self._close_task is not None and self._close_task.done()

    @property
    def counts(self) -> JournalCounts:
        """Return a point-in-time admission and persistence report."""
        return JournalCounts(
            self._received,
            self._accepted,
            self._persisted,
            self._failed,
            self._rejected,
        )

    def start(self) -> None:
        """Start the sole writer inside a running event loop, once per owner."""
        if self._writer is not None or self._closing:
            raise RuntimeError("Journal already started or closed")
        self._writer = asyncio.create_task(self._write_loop())

    def submit(self, record: VoiceJournalRecord) -> Submission:
        """Accept without awaiting; overflow records a gap for the writer."""
        self._received += 1
        if self._closing or self._writer is None:
            self._rejected += 1
            return Submission.CLOSED
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._rejected += 1
            self._mark_loss(record, GapReason.WRITER_OVERFLOW)
            return Submission.FULL
        self._accepted += 1
        return Submission.ACCEPTED

    async def close(self) -> None:
        """Stop admission and drain accepted records; raise on any write failure.

        Cancellation of the caller does not cancel a worker performing physical
        I/O. A later close call can still await the same drain operation.
        """
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain())
        cancellation: asyncio.CancelledError | None = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as error:
                cancellation = error
        self._close_task.result()
        if cancellation is not None:
            raise cancellation

    async def _drain(self) -> None:
        if self._writer is not None:
            await self._queue.put(None)
            await self._writer
        if self._write_error is not None:
            raise JournalWriteError(
                "Voice journal had failed writes"
            ) from self._write_error

    def _mark_loss(self, record: VoiceJournalRecord, reason: GapReason) -> None:
        guild_id = None if reason is GapReason.WRITE_FAILURE else record.guild_id
        start = record.observed_at
        known_bounds = True
        if isinstance(record.fact, ObservationGap) and record.fact.started_at <= start:
            start = record.fact.started_at
            known_bounds = record.fact.known_bounds
        previous = self._losses.get(guild_id)
        if previous is not None and isinstance(previous.fact, ObservationGap):
            if previous.fact.started_at < start:
                start = previous.fact.started_at
                known_bounds = previous.fact.known_bounds
            elif previous.fact.started_at == start:
                known_bounds = known_bounds and previous.fact.known_bounds
            if previous.fact.reason is GapReason.WRITE_FAILURE:
                reason = GapReason.WRITE_FAILURE
        self._losses[guild_id] = replace(
            record,
            guild_id=guild_id,
            fact=ObservationGap(
                start, None, reason, guild_id, known_bounds=known_bounds
            ),
        )

    async def _write_loop(self) -> None:
        stopped = False
        while not stopped:
            first = await self._queue.get()
            batch: list[VoiceJournalRecord] = []
            if first is not None:
                batch.append(first)
            stopped = first is None
            while not stopped and len(batch) < self._batch_size:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    stopped = True
                else:
                    batch.append(item)
            await self._persist(batch)
        if self._losses:
            await self._persist(())

    async def _persist(self, batch: Sequence[VoiceJournalRecord]) -> None:
        losses, self._losses = self._losses, {}
        records = [*batch, *losses.values()]
        if not records:
            return
        try:
            await self.store.append(uuid4().hex, records)
        except Exception as exc:
            self._write_error = exc
            self._failed += len(batch)
            # An unconfirmed commit may have persisted this batch. Keep the
            # failure window conservative until a new authoritative snapshot.
            start = min(
                r.fact.started_at
                if isinstance(r.fact, ObservationGap)
                else r.observed_at
                for r in records
            )
            last = max(records, key=lambda record: record.sequence)
            self._mark_loss(replace(last, observed_at=start), GapReason.WRITE_FAILURE)
            loss = self._losses[None]
            self._losses[None] = replace(
                loss,
                observed_at=last.observed_at,
                monotonic=last.monotonic,
            )
            logger.warning(
                "Voice journal write failed: batch=%d error=%s",
                len(batch),
                type(exc).__name__,
            )
            logger.debug("Voice journal write traceback", exc_info=True)
        else:
            self._persisted += len(batch)

    async def read_day(
        self, guild_id: int | None, day: date
    ) -> tuple[VoiceJournalRecord, ...]:
        """Read a UTC day without flushing the queue or inventing preceding coverage."""
        return await self.store.read_all(guild_id, day)

    async def read_all(self, guild_id: int | None) -> tuple[VoiceJournalRecord, ...]:
        """Read committed scope history; pending queue entries remain unconfirmed."""
        return await self.store.read_all(guild_id)

    async def snapshot_for_guild(self, guild_id: int) -> VoiceJournalSnapshot:
        """Read guild/shared facts and their revision in one SQL snapshot."""
        return await self.store.snapshot_for_guild(guild_id)

    async def revision(self, guild_id: int) -> int:
        """Return a committed scoped revision, independent of queue telemetry."""
        return await self.store.revision(guild_id)
