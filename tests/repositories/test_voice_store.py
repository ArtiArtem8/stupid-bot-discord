"""SQLite voice facts preserve replay, nullable flags and batch idempotency."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from sqlalchemy import select

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
from repositories.voice_store import VoiceStore
from tests.api.voice.examples import at, human, record
from utils.asyncio_utils import run_in_thread


class TestVoiceStore(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "voice.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.store = VoiceStore(self.database)

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
        journal = VoiceJournal(self.store, batch_size=2)
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
