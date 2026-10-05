"""Validate experimental batch identity and transaction outcomes, not timings."""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import override

from sqlalchemy import Connection, event, select
from sqlalchemy.exc import OperationalError

from api.voice.model import GapReason, ObservationGap, VoiceCheckpoint, VoiceSnapshot
from experiments.birthday_sqlite.database import migrate, open_engine
from experiments.voice_sqlite.models import verify_models
from experiments.voice_sqlite.store import TrialVoiceStore, facts, open_reader
from experiments.voice_sqlite.workload import verify_backup
from tests.api.voice.examples import at, human, record
from utils.asyncio_utils import run_in_thread


class TestVoiceSQLiteTrial(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "voice.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.engine = open_engine(self.path)
        self.addAsyncCleanup(self.engine.dispose)
        self.store = TrialVoiceStore(self.engine)
        await self.store.initialize()
        self.records = (
            record(0, VoiceSnapshot((human(), human(2)))),
            record(10, VoiceCheckpoint(), guild=None),
            record(
                10,
                ObservationGap(at(10), at(12), GapReason.WRITE_FAILURE, 1),
                sequence=100,
            ),
            record(12, VoiceSnapshot((human(), human(2)))),
            record(20, VoiceCheckpoint(), guild=None),
        )

    async def test_retry_preserves_same_sequence_loss_and_models(self) -> None:
        results = await asyncio.gather(
            *(self.store.append("first", self.records) for _ in range(5))
        )
        self.assertEqual(sum(result.inserted for result in results), 1)
        snapshot = await self.store.snapshot()
        self.assertEqual(snapshot.records, self.records)
        self.assertEqual(snapshot.revision, len(self.records))
        self.assertEqual(
            await asyncio.to_thread(verify_models, self.records, snapshot.records), 2
        )
        with self.assertRaisesRegex(ValueError, "different content"):
            await self.store.append("first", self.records[:-1])
        self.assertEqual((await self.store.snapshot()).records, self.records)

    async def test_scoped_snapshot_includes_shared_facts(self) -> None:
        other = record(21, VoiceSnapshot((human(3),)), guild=2)
        await self.store.append("first", (*self.records, other))
        self.assertEqual((await self.store.snapshot(1)).records, self.records)
        expected = tuple(r for r in (*self.records, other) if r.guild_id in (None, 2))
        self.assertEqual((await self.store.snapshot(2)).records, expected)

    async def test_cancel_during_commit_is_whole_and_retryable(self) -> None:
        loop = asyncio.get_running_loop()

        def cancel_at_commit(_connection: Connection) -> None:
            loop.call_soon(task.cancel)

        task = asyncio.create_task(self.store.append("ambiguous", self.records))
        event.listen(self.engine.sync_engine, "commit", cancel_at_commit)
        try:
            try:
                await asyncio.wait_for(task, 10)
            except asyncio.CancelledError:
                pass
        finally:
            event.remove(self.engine.sync_engine, "commit", cancel_at_commit)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        before = (await self.store.snapshot()).records
        self.assertIn(len(before), (0, len(self.records)))
        await self.store.append("ambiguous", self.records)
        self.assertEqual((await self.store.snapshot()).records, self.records)

    async def test_cancel_active_stream_releases_connection_for_writer(self) -> None:
        await self.store.append("first", self.records)
        reading = asyncio.Event()
        release = asyncio.Event()

        async def active_reader() -> None:
            async with self.engine.begin() as connection:
                async with connection.stream_scalars(select(facts.c.payload)) as result:
                    await anext(result)
                    reading.set()
                    await release.wait()

        task = asyncio.create_task(active_reader())
        try:
            await asyncio.wait_for(reading.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            extra = record(30, VoiceCheckpoint(), guild=None)
            await asyncio.wait_for(self.store.append("after-read", (extra,)), 5)
            self.assertEqual(
                (await self.store.snapshot()).records, (*self.records, extra)
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_waiting_read_does_not_block_next_write(self) -> None:
        queued = asyncio.Event()

        async def waiting_reader() -> None:
            queued.set()
            await self.store.snapshot()

        async with self.engine.begin():
            task = asyncio.create_task(waiting_reader())
            try:
                await asyncio.wait_for(queued.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((await self.store.snapshot()).records, ())
        await self.store.append("waiting", self.records)
        self.assertEqual((await self.store.snapshot()).records, self.records)

    async def test_fresh_process_reopens_and_resolves_retry(self) -> None:
        await self.store.append("restart", self.records)
        await self.engine.dispose()
        script = """
import asyncio, sys
from pathlib import Path
from experiments.birthday_sqlite.database import open_engine
from experiments.voice_sqlite.store import TrialVoiceStore
async def check():
    engine = open_engine(Path(sys.argv[1]))
    try:
        store = TrialVoiceStore(engine)
        before = await store.snapshot()
        if len(before.records) != 5:
            raise RuntimeError('Restart lost committed facts')
        outcome = await store.append('restart', before.records)
        after = await store.snapshot()
        if outcome.inserted or after.records != before.records:
            raise RuntimeError('Restart retry duplicated facts')
    finally:
        await engine.dispose()
asyncio.run(check())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(self.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 15)
            self.assertEqual(process.returncode, 0, (output + errors).decode())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_query_only_reader_keeps_snapshot_without_blocking_writer(
        self,
    ) -> None:
        await self.store.append("first", self.records)
        reader = open_reader(self.path)
        try:
            async with reader.begin() as connection:
                before = tuple(
                    (await connection.scalars(select(facts.c.ordinal))).all()
                )
                extra = record(30, VoiceCheckpoint(), guild=None)
                await asyncio.wait_for(self.store.append("parallel", (extra,)), 5)
                self.assertEqual(
                    tuple((await connection.scalars(select(facts.c.ordinal))).all()),
                    before,
                )
            actual = await TrialVoiceStore(self.engine, reader).snapshot()
            self.assertEqual(actual.records, (*self.records, extra))
            with self.assertRaises(OperationalError):
                async with reader.begin() as connection:
                    await connection.execute(facts.delete())
        finally:
            await reader.dispose()

    async def test_restore_verification_rejects_foreign_keys_and_wrong_revision(
        self,
    ) -> None:
        await run_in_thread(lambda: verify_backup(self.path))
        await self.engine.dispose()

        def inject_orphan() -> None:
            with closing(sqlite3.connect(self.path)) as connection:
                connection.execute(
                    "INSERT INTO birthday_users VALUES (999, 1, 'N', '', 0)"
                )
                connection.commit()

        await run_in_thread(inject_orphan)
        with self.assertRaisesRegex(ValueError, "foreign key"):
            await run_in_thread(lambda: verify_backup(self.path))

        def change_revision() -> None:
            with closing(sqlite3.connect(self.path)) as connection:
                connection.execute("DELETE FROM birthday_users")
                connection.execute("UPDATE alembic_version SET version_num='unknown'")
                connection.commit()

        await run_in_thread(change_revision)
        with self.assertRaisesRegex(ValueError, "revision"):
            await run_in_thread(lambda: verify_backup(self.path))
