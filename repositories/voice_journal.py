"""Single-owner append-only voice journal with bounded buffering.

New writes use root/v2/{session,guild_ID}/events_UTC-DATE.jsonl. Legacy files
remain read-only. Keep one instance for a root for the entire collector lifetime.
Submission is synchronous and preserves event order without blocking Discord.
Only fsynced batches advance persisted; close drains in-flight work without
cancelling the writer. A partial append is not retried and opens a conservative
write-failure gap. Readers fail explicitly on corrupt files.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path

from api.voice.model import GapReason, ObservationGap, VoiceJournalRecord
from repositories._voice_codec import decode_record, encode_record

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
    """Own queue, writer lifecycle and serialized file maintenance for one root."""

    def __init__(
        self, root: Path, *, queue_size: int = 10_000, batch_size: int = 500
    ) -> None:
        if queue_size < 1 or batch_size < 1:
            raise ValueError("Queue and batch sizes must be positive")
        self.root = root
        self._queue: asyncio.Queue[VoiceJournalRecord | None] = asyncio.Queue(
            queue_size
        )
        self._batch_size = batch_size
        self._writer: asyncio.Task[None] | None = None
        self._closing = False
        # Without this lock, maintenance could gzip a file while the writer's
        # worker thread appends to it, losing the newly appended bytes.
        self._files = asyncio.Lock()
        self._received = self._accepted = self._persisted = 0
        self._failed = self._rejected = 0
        self._loss: VoiceJournalRecord | None = None
        self._write_error: Exception | None = None
        self._close_task: asyncio.Task[None] | None = None

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
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        if self._writer is not None:
            await self._queue.put(None)
            await self._writer
        if self._write_error is not None:
            raise JournalWriteError(
                "Voice journal had failed writes"
            ) from self._write_error

    def _mark_loss(self, record: VoiceJournalRecord, reason: GapReason) -> None:
        start = record.observed_at
        if self._loss is not None and isinstance(self._loss.fact, ObservationGap):
            start = min(start, self._loss.fact.started_at)
        self._loss = replace(
            record,
            guild_id=None,
            fact=ObservationGap(
                start,
                None,
                reason,
                known_bounds=False,
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
        if self._loss is not None:
            await self._persist(())

    async def _persist(self, batch: Sequence[VoiceJournalRecord]) -> None:
        loss, self._loss = self._loss, None
        records = [*batch]
        if loss is not None:
            records.append(loss)
        if not records:
            return
        try:
            await self._file_work(partial(self._append, records))
        except Exception as exc:
            self._write_error = exc
            self._failed += len(batch)
            # A worker may have appended only part of a batch. Invalidate from
            # its first fact through recovery, but never retry those facts.
            start = min(
                r.fact.started_at
                if isinstance(r.fact, ObservationGap)
                else r.observed_at
                for r in records
            )
            self._mark_loss(
                replace(records[-1], observed_at=start), GapReason.WRITE_FAILURE
            )
            if self._loss is not None:
                self._loss = replace(
                    self._loss,
                    observed_at=records[-1].observed_at,
                    monotonic=records[-1].monotonic,
                )
            logger.warning(
                "Voice journal write failed: batch=%d error=%s",
                len(batch),
                type(exc).__name__,
            )
            logger.debug("Voice journal write traceback", exc_info=True)
        else:
            self._persisted += len(batch)

    def _path_for(self, record: VoiceJournalRecord) -> Path:
        return self._day_path(
            self.root / "v2", record.guild_id, record.observed_at.astimezone(UTC).date()
        )

    @staticmethod
    def _day_path(root: Path, guild_id: int | None, day: date) -> Path:
        area = "session" if guild_id is None else f"guild_{guild_id}"
        return root / area / f"events_{day.isoformat()}.jsonl"

    def _append(self, records: Sequence[VoiceJournalRecord]) -> None:
        batches: dict[Path, list[str]] = {}
        for record in records:
            batches.setdefault(self._path_for(record), []).append(encode_record(record))
        for path, lines in batches.items():
            if path.with_suffix(".jsonl.gz").exists():
                raise OSError("Cannot append to an archived journal day")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("".join(f"{line}\n" for line in lines))
                handle.flush()
                os.fsync(handle.fileno())

    async def read_day(
        self, guild_id: int | None, day: date
    ) -> tuple[VoiceJournalRecord, ...]:
        """Read persisted legacy and v2 facts; no implicit queue flush.

        Include session records when building a guild timeline. A day slice
        without a preceding authoritative snapshot intentionally starts unknown.
        """
        return await self._file_work(partial(self._read_day, guild_id, day))

    def _read_day(
        self, guild_id: int | None, day: date
    ) -> tuple[VoiceJournalRecord, ...]:
        records: list[VoiceJournalRecord] = []
        for root in (self.root, self.root / "v2"):
            path = self._day_path(root, guild_id, day)
            compressed = path.with_suffix(".jsonl.gz")
            if compressed.exists():
                with gzip.open(compressed, "rt", encoding="utf-8") as handle:
                    records.extend(
                        decode_record(line) for line in handle if line.strip()
                    )
            elif path.exists():
                with path.open(encoding="utf-8") as handle:
                    records.extend(
                        decode_record(line) for line in handle if line.strip()
                    )
        return tuple(records)

    async def compact(self, *, before_day: date) -> int:
        """Atomically archive finished v2 days only; never modify legacy files."""
        return await self._file_work(partial(self._compact, before_day))

    def _compact(self, before_day: date) -> int:
        count = 0
        for path in (self.root / "v2").glob("*/events_*.jsonl"):
            day = _day_from_name(path.name)
            if day is None or day >= before_day:
                continue
            target = path.with_suffix(".jsonl.gz")
            temporary = target.with_suffix(".gz.tmp")
            try:
                with path.open("rb") as source, gzip.open(temporary, "wb") as sink:
                    shutil.copyfileobj(source, sink)
                temporary.replace(target)
                path.unlink()
            finally:
                temporary.unlink(missing_ok=True)
            count += 1
        return count

    async def prune(self, *, today: date, retention_days: int) -> int:
        """Remove expired v2 days only; retain at least one day."""
        if retention_days < 1:
            raise ValueError("Retention must be positive")
        return await self._file_work(
            partial(self._prune, today - timedelta(days=retention_days))
        )

    async def _file_work[T](self, operation: Callable[[], T]) -> T:
        # Cancellation must not release the file lock while its worker thread
        # still accesses a day file (notably during collector unload).
        async with self._files:
            work = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise

    def _prune(self, cutoff: date) -> int:
        count = 0
        for path in (self.root / "v2").glob("*/events_*.jsonl*"):
            day = _day_from_name(path.name)
            if day is not None and day < cutoff:
                path.unlink()
                count += 1
        return count


def _day_from_name(name: str) -> date | None:
    raw = name.removeprefix("events_").removesuffix(".gz").removesuffix(".jsonl")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
