"""Commit/publication interleavings against a disposable real SQLite database."""

import asyncio
import unittest
from collections.abc import Sequence
from typing import override
from unittest.mock import patch

from sqlalchemy import event, text

from stupidbot.features.voice.facts import (
    VoiceCheckpoint,
    VoiceJournalRecord,
    VoiceSnapshot,
)
from stupidbot.features.voice.journal import VoiceJournal
from stupidbot.features.voice.repository import (
    VoiceCommit,
    VoiceHistory,
    VoiceRepository,
)
from stupidbot.features.voice.runtime import AnalyticsUnavailableError, VoiceAnalytics
from stupidbot.features.voice.timeline import (
    VoiceReplayState,
    VoiceTimeline,
    build_timeline,
)
from tests.features.voice.fixtures import human, record
from tests.storage.fixtures import temporary_database


class TestVoiceAnalytics(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.store = VoiceRepository(self.database)
        self.initial = record(0, VoiceSnapshot((human(),)))
        await self.store.append("initial", [self.initial])
        self.analytics = VoiceAnalytics(self.store)
        self.addAsyncCleanup(self.analytics.close)
        await self.analytics.start()

    async def test_fast_append_retry_and_snapshot_do_not_read_history(self) -> None:
        before = await self.analytics.snapshot(1)
        fact = record(10, VoiceCheckpoint())
        with patch.object(
            self.store, "history", side_effect=AssertionError("Full read")
        ):
            revision = await self.analytics.append("next", [fact])
            self.assertEqual(await self.analytics.append("next", [fact]), revision)
            after = await self.analytics.snapshot(1)
            self.assertEqual(after.timeline, build_timeline([self.initial, fact]))
            self.assertEqual(after.generation, revision)
            self.assertEqual(before.timeline.rooms, ())
            self.assertEqual((await self.analytics.snapshot(2)).generation, 0)
        self.assertEqual(len((await self.store.history()).records), 2)

    async def test_ready_snapshot_executes_no_sql(self) -> None:
        statements: list[str] = []

        def traced(
            _c: object, _cu: object, sql: str, _p: object, _ctx: object, _many: object
        ) -> None:
            statements.append(sql)

        event.listen(self.database.engine.sync_engine, "before_cursor_execute", traced)
        try:
            await self.analytics.snapshot(1)
            await self.analytics.snapshot()
        finally:
            event.remove(
                self.database.engine.sync_engine, "before_cursor_execute", traced
            )
        self.assertEqual(statements, [])

    async def test_snapshot_cache_is_scoped_bounded_and_immutable(self) -> None:
        await self.analytics.append(
            "establish-other", [record(1, VoiceSnapshot(()), guild=2)]
        )
        await self.analytics.start()
        original = VoiceReplayState.snapshot
        builds: list[int | None] = []

        def counted(
            state: VoiceReplayState, guild_id: int | None = None
        ) -> VoiceTimeline:
            builds.append(guild_id)
            return original(state, guild_id)

        with patch.object(VoiceReplayState, "snapshot", new=counted):
            first = await self.analytics.snapshot(1)
            self.assertIs(await self.analytics.snapshot(1), first)
            await self.analytics.append(
                "other", [record(10, VoiceCheckpoint(), guild=2)]
            )
            self.assertIs(await self.analytics.snapshot(1), first)
            self.assertEqual(builds, [1])
            await self.analytics.append("local", [record(20, VoiceCheckpoint())])
            second = await self.analytics.snapshot(1)
            self.assertIsNot(second, first)
            self.assertEqual(first.timeline.rooms, ())
            await self.analytics.append(
                "shared", [record(30, VoiceCheckpoint(), guild=None)]
            )
            self.assertIsNot(await self.analytics.snapshot(1), second)
            for guild in range(2, 10):
                await self.analytics.snapshot(guild)
            await self.analytics.snapshot(1)
            self.assertEqual(builds.count(1), 4)

    async def test_recovery_and_new_owner_cannot_reuse_old_snapshot(self) -> None:
        first = await self.analytics.snapshot(1)
        with patch.object(self.store, "append_batch", side_effect=OSError("unknown")):
            with self.assertRaises(OSError):
                await self.analytics.append("unknown", [record(10, VoiceCheckpoint())])
        with self.assertRaises(AnalyticsUnavailableError):
            await self.analytics.snapshot(1)
        await self.analytics.start()
        self.assertIsNot(await self.analytics.snapshot(1), first)
        await self.analytics.close()
        with self.assertRaises(AnalyticsUnavailableError):
            await self.analytics.snapshot(1)
        replacement = VoiceAnalytics(self.store)
        self.addAsyncCleanup(replacement.close)
        await replacement.start()
        self.assertIsNot(await replacement.snapshot(1), first)

    async def test_apply_failure_preserves_journal_success_and_recovery(self) -> None:
        original = VoiceReplayState.apply_many

        def fail_nonempty(
            state: VoiceReplayState, records: Sequence[VoiceJournalRecord]
        ) -> bool:
            if records:
                raise ValueError("derived state failure")
            return original(state, records)

        journal = VoiceJournal(self.analytics)
        with patch.object(VoiceReplayState, "apply_many", new=fail_nonempty):
            with self.assertLogs("stupidbot.features.voice.runtime", level="WARNING"):
                journal.start()
                journal.submit(record(10, VoiceCheckpoint()))
                await journal.close()
        self.assertEqual(journal.counts.persisted, 1)
        self.assertEqual(journal.counts.failed, 0)
        await self.analytics.start()
        self.assertEqual(
            (await self.analytics.snapshot()).timeline,
            build_timeline((await self.store.history()).records),
        )

    async def test_commit_during_rebuild_is_caught_up_before_publication(self) -> None:
        await self._commit_during_rebuild(record(20, VoiceCheckpoint()))

    async def test_unsafe_commit_during_rebuild_uses_final_full_cutoff(self) -> None:
        await self._commit_during_rebuild(record(20, VoiceSnapshot(()), boot="new"))

    async def _commit_during_rebuild(self, late_fact: VoiceJournalRecord) -> None:
        await self.analytics.close()
        analytics = VoiceAnalytics(self.store)
        self.addAsyncCleanup(analytics.close)
        self.analytics = analytics
        loaded, release = asyncio.Event(), asyncio.Event()
        history = self.store.history
        calls = 0

        async def pause(*, after_record_id: int = 0) -> VoiceHistory:
            nonlocal calls
            result = await history(after_record_id=after_record_id)
            calls += 1
            if calls == 1:
                loaded.set()
                await release.wait()
            return result

        with patch.object(self.store, "history", side_effect=pause):
            starting = asyncio.create_task(analytics.start())
            await loaded.wait()
            with self.assertRaises(AnalyticsUnavailableError):
                await analytics.snapshot(1)
            await analytics.append(f"late-{late_fact.boot_id}", [late_fact])
            release.set()
            await starting
        expected = await history()
        result = await analytics.snapshot()
        self.assertEqual(result.timeline, build_timeline(expected.records))
        self.assertEqual(result.generation, expected.cutoff.revision)

    async def test_cancellation_after_sql_commit_invalidates_then_recovers(
        self,
    ) -> None:
        committed, release = asyncio.Event(), asyncio.Event()
        append = self.store.append_batch

        async def interrupted(
            token: str, records: Sequence[VoiceJournalRecord]
        ) -> VoiceCommit:
            receipt = await append(token, records)
            committed.set()
            await release.wait()
            return receipt

        with patch.object(self.store, "append_batch", side_effect=interrupted):
            writing = asyncio.create_task(
                self.analytics.append("ambiguous", [record(10, VoiceCheckpoint())])
            )
            await committed.wait()
            writing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await writing
        with self.assertRaises(AnalyticsUnavailableError):
            await self.analytics.snapshot(1)
        await self.analytics.start()
        revision = await self.analytics.append(
            "ambiguous", [record(10, VoiceCheckpoint())]
        )
        snapshot = await self.analytics.snapshot(1)
        self.assertEqual(snapshot.generation, revision)
        self.assertEqual(len((await self.store.history()).records), 2)

    async def test_noncontiguous_record_ids_and_restart(self) -> None:
        _, database = await temporary_database(self)
        store = VoiceRepository(database)
        await store.append("shared", [record(0, VoiceCheckpoint(), guild=None)])
        async with database.transaction() as connection:
            await connection.execute(
                text("UPDATE voice_records SET record_id=100 WHERE record_id=1")
            )
        initial = await store.history()
        self.assertEqual(initial.cutoff.last_record_id, 100)
        receipt = await store.append_batch(
            "guild", [record(1, VoiceSnapshot((human(),)))]
        )
        tail = await store.history(after_record_id=100)
        self.assertEqual(len(tail.records), 1)
        self.assertGreater(receipt.last_record_id, 100)
        for _ in range(2):
            analytics = VoiceAnalytics(store)
            try:
                await analytics.start()
                self.assertEqual(
                    (await analytics.snapshot()).timeline,
                    build_timeline((await store.history()).records),
                )
                await analytics.append("checkpoint", [record(10, VoiceCheckpoint())])
            finally:
                await analytics.close()

    async def test_cancelled_start_and_repeated_close_drain_recovery(self) -> None:
        await self.analytics.close()
        analytics = VoiceAnalytics(self.store)
        entered, release = asyncio.Event(), asyncio.Event()
        history = self.store.history

        async def hold(*, after_record_id: int = 0) -> VoiceHistory:
            entered.set()
            await release.wait()
            return await history(after_record_id=after_record_id)

        with patch.object(self.store, "history", side_effect=hold):
            start = asyncio.create_task(analytics.start())
            await entered.wait()
            start.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await start
            first = asyncio.create_task(analytics.close())
            second = asyncio.create_task(analytics.close())
            scheduled = asyncio.Event()
            asyncio.get_running_loop().call_soon(scheduled.set)
            await scheduled.wait()
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)
        with self.assertRaises(AnalyticsUnavailableError):
            await analytics.snapshot()
