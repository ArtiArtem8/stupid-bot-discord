"""Measure a finite mixed workload; never run timing thresholds in CI."""

import asyncio
import math
import sqlite3
import tracemalloc
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from time import perf_counter

from api.voice.model import VoiceJournalRecord
from api.voice.timeline import build_timeline
from experiments.birthday_sqlite.database import copy_database, migrate, open_engine
from experiments.birthday_sqlite.repository import SQLiteBirthdayRepository
from experiments.voice_sqlite.models import contexts, project, verify_models
from experiments.voice_sqlite.store import TrialVoiceStore, open_reader
from utils.asyncio_utils import run_in_thread
from utils.json_types import JsonObject

_BATCH = 500


def summarize(values: Sequence[float]) -> JsonObject:
    """Report nearest-rank percentiles in the input units, without interpolation."""
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(ordered),
        "p50": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max": ordered[-1],
    }


def _wal_bytes(path: Path) -> int:
    wal = Path(str(path) + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def verify_backup(path: Path) -> None:
    """Require structural integrity, foreign keys and the expected birthday schema."""
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Backup integrity mismatch")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Backup foreign key mismatch")
        if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [
            ("0001_birthdays",)
        ]:
            raise ValueError("Backup schema revision mismatch")


class MixedWorkload:
    """Own measurements for one disposable database and a finite task group."""

    def __init__(
        self,
        store: TrialVoiceStore,
        path: Path,
        records: tuple[VoiceJournalRecord, ...],
        reads: int,
    ) -> None:
        self.store, self.path, self.records, self.reads = store, path, records, reads
        self.write_wait: list[float] = []
        self.commit: list[float] = []
        self.read_wait: list[float] = []
        self.cold: list[float] = []
        self.warm: list[float] = []
        self.birthday: list[float] = []
        self.backup: list[float] = []
        self.lag: list[float] = []
        self.wal: list[float] = []
        self.backups: list[Path] = []
        self.boundaries: set[int] = {0}
        self.first_write = asyncio.Event()
        self.stop = asyncio.Event()

    async def writer(self, start: int) -> None:
        """Append only the remaining frozen facts, with stable per-batch tokens."""
        for offset in range(start, len(self.records), _BATCH):
            batch = self.records[offset : offset + _BATCH]
            sample = await self.store.append(f"source:{offset}", batch)
            self.boundaries.add(offset + len(batch))
            self.write_wait.append(sample.wait_ms)
            self.commit.append(sample.commit_ms)
            self.first_write.set()

    async def reader(self, guild_id: int, user_id: int) -> None:
        """Measure uncached reads and repeated projection from a detached timeline."""
        as_of = max(record.observed_at for record in self.records)
        for _ in range(self.reads):
            started = perf_counter()
            snapshot = await self.store.snapshot(guild_id)
            self.read_wait.append(snapshot.wait_ms)
            timeline = await asyncio.to_thread(build_timeline, snapshot.records)
            before = await asyncio.to_thread(
                project, timeline, guild_id, user_id, as_of
            )
            self.cold.append((perf_counter() - started) * 1000)
            started = perf_counter()
            after = await asyncio.to_thread(project, timeline, guild_id, user_id, as_of)
            self.warm.append((perf_counter() - started) * 1000)
            if before != after:
                raise ValueError("Repeated pinned projection changed")

    async def birthdays(self) -> None:
        """Mix twenty synthetic setting changes through the same engine pool."""
        repository = SQLiteBirthdayRepository(self.store.engine)
        for channel in range(20):
            started = perf_counter()
            await repository.configure_guild(1, "Synthetic workload", channel, None)
            self.birthday.append((perf_counter() - started) * 1000)

    async def backup_task(self) -> None:
        """Take three live snapshots using SQLite's separate backup connection."""
        await self.first_write.wait()
        for index in range(3):
            destination = self.path.with_name(f"backup-{index}.sqlite")
            started = perf_counter()
            await run_in_thread(
                lambda destination=destination: copy_database(self.path, destination)
            )
            self.backup.append((perf_counter() - started) * 1000)
            self.backups.append(destination)

    async def monitor(self) -> None:
        """Sample event-loop scheduling delay and WAL size during concurrent work."""
        while not self.stop.is_set():
            target = perf_counter() + 0.01
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.01)
            except TimeoutError:
                self.lag.append(max(0, perf_counter() - target) * 1000)
                self.wal.append(float(await asyncio.to_thread(_wal_bytes, self.path)))

    async def run(self, start: int, pairs: tuple[tuple[int, int], ...]) -> None:
        """Run one writer, two readers, birthday changes, backup and monitoring."""
        monitor = asyncio.create_task(self.monitor())
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.writer(start))
                for gid, uid in pairs[:2]:
                    tasks.create_task(self.reader(gid, uid))
                tasks.create_task(self.birthdays())
                tasks.create_task(self.backup_task())
        finally:
            self.stop.set()
            await monitor

    def report(self) -> JsonObject:
        """Return aggregate measurements without member identities or payloads."""
        return {
            "write_checkout_ms": summarize(self.write_wait),
            "commit_context_ms": summarize(self.commit),
            "read_checkout_ms": summarize(self.read_wait),
            "uncached_profile_ms": summarize(self.cold),
            "reused_timeline_projection_ms": summarize(self.warm),
            "birthday_operation_ms": summarize(self.birthday),
            "backup_ms": summarize(self.backup),
            "loop_lag_ms": summarize(self.lag),
            "wal_bytes": summarize(self.wal),
            "errors": 0,
        }


async def run_once(
    records: tuple[VoiceJournalRecord, ...],
    directory: Path,
    reads: int,
    *,
    verify: bool,
    separate_reader: bool = False,
) -> JsonObject:
    """Prime 80% of input, mix the remaining batches with reads, then verify."""
    path = directory / "trial.sqlite"
    await run_in_thread(lambda: migrate(path))
    engine = open_engine(path)
    reader = open_reader(path) if separate_reader else None
    store = TrialVoiceStore(engine, reader)
    try:
        await store.initialize()
        split = max(1, len(records) * 4 // 5)
        workload = MixedWorkload(store, path, records, reads)
        for offset in range(0, split, _BATCH):
            batch = records[offset : min(offset + _BATCH, split)]
            await store.append(f"prime:{offset}", batch)
            workload.boundaries.add(offset + len(batch))
        timeline = await asyncio.to_thread(build_timeline, records)
        pairs = contexts(timeline)
        if not pairs:
            raise ValueError("Workload requires observed member contexts")
        await workload.run(split, pairs)
        snapshot = await store.snapshot()
        if snapshot.records != records:
            raise ValueError("Final history differs from frozen input")
        checked = (
            await asyncio.to_thread(verify_models, records, snapshot.records)
            if verify
            else 0
        )
        for backup in workload.backups:
            await run_in_thread(lambda backup=backup: verify_backup(backup))
            backup_engine = open_engine(backup)
            try:
                copied = (await TrialVoiceStore(backup_engine).snapshot()).records
                if (
                    len(copied) not in workload.boundaries
                    or copied != records[: len(copied)]
                ):
                    raise ValueError("Backup is not a complete committed batch prefix")
                await SQLiteBirthdayRepository(backup_engine).get_all()
            finally:
                await backup_engine.dispose()
        report = workload.report()
        report["model_contexts_verified"] = checked
        report["records"] = len(records)
        report["batch_size"] = _BATCH
        report["reader_tasks"] = min(2, len(pairs))
        report["wal_final_bytes"] = await asyncio.to_thread(_wal_bytes, path)
        # This separate allocation probe is excluded from latency measurements.
        tracemalloc.start()
        try:
            memory_snapshot = await store.snapshot(pairs[0][0])
            memory_timeline = await asyncio.to_thread(
                build_timeline, memory_snapshot.records
            )
            await asyncio.to_thread(
                project, memory_timeline, *pairs[0], max(r.observed_at for r in records)
            )
            report["separate_cold_read_python_peak_bytes"] = (
                tracemalloc.get_traced_memory()[1]
            )
        finally:
            tracemalloc.stop()
        async with engine.begin() as connection:
            for pragma in (
                "journal_mode",
                "foreign_keys",
                "synchronous",
                "busy_timeout",
                "wal_autocheckpoint",
            ):
                value: object = (
                    await connection.exec_driver_sql("PRAGMA " + pragma)
                ).scalar()
                report[pragma] = str(value)
        return report
    finally:
        if reader is not None:
            await reader.dispose()
        await engine.dispose()
