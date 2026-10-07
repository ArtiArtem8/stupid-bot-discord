"""SQLite voice facts preserve replay, nullable flags and batch idempotency."""

import asyncio
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from sqlalchemy import select

from api.voice.analytics import VoiceAnalytics
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.timeline import build_timeline
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.schema import voice_batches
from repositories.voice_journal import Submission, VoiceJournal
from repositories.voice_repository import VoiceRepository
from tests.api.voice.examples import at, human, record
from utils.asyncio_utils import run_in_thread


class TestVoiceRepository(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "voice.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.store = VoiceRepository(self.database)
        self.analytics = VoiceAnalytics(self.store)
        self.addAsyncCleanup(self.analytics.close)
        await self.analytics.start()

    async def test_round_trip_and_equal_sequence_gap_preserve_timeline(self) -> None:
        state = VoiceStateSnapshot(
            1,
            10,
            is_bot=None,
            self_mute=True,
            self_deaf=False,
            server_mute=None,
            server_deaf=True,
            self_stream=True,
            self_video=False,
            suppress=False,
            requested_to_speak_at=at(1),
            session_id="s",
            afk=None,
        )
        facts = (
            record(0, VoiceLifecycle(), guild=None),
            record(1, VoiceSnapshot((state,))),
            record(2, VoiceObservation(human(2))),
            record(2, ObservationGap(at(2), None, GapReason.WRITER_OVERFLOW, 1)),
            record(3, VoiceCheckpoint(), guild=None),
            record(4, VoiceSnapshot(())),
            record(5, VoiceLifecycle(stopped=True), guild=None),
        )
        revision = await self.store.append("one", facts)
        snapshot = await self.store.snapshot_for_guild(1)
        restored = (*snapshot.session_records, *snapshot.guild_records)
        self.assertCountEqual(restored, facts)
        self.assertEqual(snapshot.guild_records[1:3], facts[2:4])
        self.assertEqual(build_timeline(restored), build_timeline(facts))
        self.assertEqual(await self.store.append("one", facts), revision)
        with self.assertRaisesRegex(ValueError, "different content"):
            await self.store.append("one", facts[:-1])
        self.assertEqual(await self.store.snapshot_for_guild(1), snapshot)

    async def test_revision_changes_only_for_relevant_guild_and_shared_facts(
        self,
    ) -> None:
        await self.store.append("a", [record(0, VoiceSnapshot((human(),)))])
        before = await self.store.revision(1)
        await self.store.append("b", [record(0, VoiceSnapshot(()), guild=2)])
        self.assertEqual(await self.store.revision(1), before)
        await self.store.append("c", [record(5, VoiceCheckpoint(), guild=None)])
        self.assertGreater(await self.store.revision(1), before)

    async def test_failed_states_roll_back_envelopes_batch_and_revision(self) -> None:
        # First establish channel 10 in guild 1, then reject reuse by guild 2.
        await self.store.append("good", [record(0, VoiceSnapshot((human(),)))])
        before = await self.store.revision(2)
        with self.assertRaises(ValueError):
            await self.store.append(
                "bad", [record(1, VoiceSnapshot((human(),)), guild=2)]
            )
        self.assertEqual(await self.store.read_all(2), ())
        self.assertEqual(await self.store.revision(2), before)
        async with self.database.transaction() as connection:
            self.assertIsNone(
                await connection.scalar(
                    select(voice_batches.c.batch_id).where(
                        voice_batches.c.batch_id == "bad"
                    )
                )
            )

    async def test_queue_close_drains_before_database_disposal(self) -> None:
        journal = VoiceJournal(self.store, analytics=self.analytics, batch_size=2)
        journal.start()
        facts = [
            record(0, VoiceSnapshot(())),
            record(10, VoiceCheckpoint(), guild=None),
        ]
        for fact in facts:
            self.assertEqual(journal.submit(fact), Submission.ACCEPTED)
        await journal.close()
        self.assertEqual(journal.counts.persisted, 2)
        self.assertEqual(journal.submit(facts[0]), Submission.CLOSED)
        snapshot = await self.store.snapshot_for_guild(1)
        self.assertCountEqual(
            (*snapshot.guild_records, *snapshot.session_records), facts
        )

    async def test_round_trip_preserves_member_order_and_asymmetric_flags(self) -> None:
        states = (
            VoiceStateSnapshot(
                7,
                10,
                is_bot=True,
                self_mute=False,
                self_deaf=None,
                server_mute=True,
                server_deaf=False,
                self_stream=None,
                self_video=True,
                suppress=None,
                afk=False,
                requested_to_speak_at=at(2),
                session_id="first",
            ),
            VoiceStateSnapshot(
                3,
                None,
                channel_known=False,
                is_bot=False,
                self_mute=None,
                self_deaf=True,
                server_mute=False,
                server_deaf=None,
                self_stream=True,
                self_video=False,
                suppress=True,
                afk=None,
            ),
        )
        fact = record(5, VoiceSnapshot(states, authoritative=False))
        await self.store.append("named", [fact])
        self.assertEqual(await self.store.read_all(1), (fact,))

    async def test_scope_and_day_filters_retain_order_duplicates_and_states(
        self,
    ) -> None:
        facts = (
            record(0, VoiceCheckpoint(), guild=None),
            record(1, VoiceObservation(human(1))),
            record(1, VoiceObservation(human(1))),
            record(2, VoiceSnapshot((human(2, 20),)), guild=2),
            record(86400, VoiceSnapshot((human(3),))),
            record(86401, VoiceCheckpoint(), guild=None),
        )
        await self.store.append("days", facts)
        self.assertEqual(await self.store.read_all(None), (facts[0], facts[5]))
        self.assertEqual(await self.store.read_all(1, at(0).date()), facts[1:3])
        self.assertEqual(
            await self.store.read_all(1, at(0).date() + timedelta(days=1)),
            (facts[4],),
        )
        snapshot = await self.store.snapshot_for_guild(1)
        self.assertEqual(snapshot.guild_records, (facts[1], facts[2], facts[4]))
        self.assertEqual(snapshot.session_records, (facts[0], facts[5]))
        self.assertEqual(await self.store.read_all(2), (facts[3],))
        self.assertEqual(await self.store.read_all(99), ())

    async def test_concurrent_batch_retries_publish_one_revision(self) -> None:
        facts = [record(0, VoiceSnapshot((human(),))), record(1, VoiceCheckpoint())]
        revisions = await asyncio.gather(
            *(self.store.append("same", facts) for _ in range(5))
        )
        self.assertEqual(len(set(revisions)), 1)
        self.assertEqual(await self.store.read_all(1), tuple(facts))

    async def test_cancelled_pool_reader_does_not_block_next_write(self) -> None:
        queued = asyncio.Event()

        async def read() -> None:
            queued.set()
            await self.store.snapshot_for_guild(1)

        async with self.database.transaction():
            task = asyncio.create_task(read())
            await queued.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        facts = [record(0, VoiceSnapshot(()))]
        await self.store.append("after-read", facts)
        self.assertEqual(await self.store.read_all(1), tuple(facts))
