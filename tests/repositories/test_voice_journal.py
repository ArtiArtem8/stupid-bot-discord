"""Bounded voice admission and conservative gaps survive the SQL storage change."""

import asyncio
import unittest
from collections.abc import Sequence
from typing import override
from unittest.mock import patch

from api.voice.analytics import VoiceAnalytics
from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceJournalRecord,
    VoiceObservation,
    VoiceSnapshot,
)
from api.voice.timeline import build_timeline
from repositories.voice_journal import JournalWriteError, Submission, VoiceJournal
from repositories.voice_repository import VoiceRepository
from tests.api.voice.examples import START, at, human, record
from tests.storage import temporary_database


class TestVoiceJournal(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.store = VoiceRepository(database)
        self.analytics = VoiceAnalytics(self.store)
        self.addAsyncCleanup(self.analytics.close)
        await self.analytics.start()
        self.journal = VoiceJournal(self.analytics, batch_size=2)

    async def test_acceptance_is_not_persistence_and_shutdown_drains_batch(
        self,
    ) -> None:
        self.journal.start()
        for seconds in range(5):
            self.assertEqual(
                self.journal.submit(record(seconds, VoiceObservation(human()))),
                Submission.ACCEPTED,
            )
        self.assertEqual(self.journal.counts.accepted, 5)
        self.assertEqual(self.journal.counts.persisted, 0)
        await self.journal.close()
        self.assertEqual(self.journal.counts.persisted, 5)
        self.assertEqual(len(await self.store.read_all(1, START.date())), 5)
        self.assertEqual(
            self.journal.submit(record(6, VoiceCheckpoint())), Submission.CLOSED
        )
        await self.journal.close()

    async def test_write_failure_never_reports_persistence(self) -> None:
        with patch.object(
            self.analytics, "append", side_effect=OSError("disk unavailable")
        ):
            self.journal.start()
            self.journal.submit(record(0, VoiceSnapshot((human(),))))
            with self.assertLogs("repositories.voice_journal", level="WARNING"):
                with self.assertRaises(JournalWriteError):
                    await self.journal.close()
        self.assertEqual(self.journal.counts.accepted, 1)
        self.assertEqual(self.journal.counts.failed, 1)
        self.assertEqual(self.journal.counts.persisted, 0)

    async def test_recovered_writer_persists_failure_gap_without_retrying_batch(
        self,
    ) -> None:
        append = self.analytics.append
        calls = 0

        async def fail_once(token: str, records: Sequence[VoiceJournalRecord]) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient write failure")
            return await append(token, records)

        with patch.object(self.analytics, "append", side_effect=fail_once):
            self.journal.start()
            self.journal.submit(record(0, VoiceSnapshot((human(),))))
            with self.assertLogs("repositories.voice_journal", level="WARNING"):
                with self.assertRaises(JournalWriteError):
                    await self.journal.close()
        gaps = await self.store.read_all(None, START.date())
        self.assertEqual(len(gaps), 1)
        self.assertIsInstance(gaps[0].fact, ObservationGap)
        if isinstance(gaps[0].fact, ObservationGap):
            self.assertEqual(gaps[0].fact.reason, GapReason.WRITE_FAILURE)
        self.assertEqual(await self.store.read_all(1, START.date()), ())

    async def test_bounded_queue_records_overflow_gap(self) -> None:
        journal = VoiceJournal(self.analytics, queue_size=1)
        journal.start()
        self.assertEqual(
            journal.submit(record(0, VoiceSnapshot((human(),)))), Submission.ACCEPTED
        )
        self.assertEqual(
            journal.submit(record(10, VoiceObservation(human(1, None)))),
            Submission.FULL,
        )
        await journal.close()
        self.assertEqual(journal.counts.rejected, 1)
        records = (
            *await self.store.read_all(1, START.date()),
            *await self.store.read_all(None, START.date()),
        )
        timeline = build_timeline(records)
        self.assertEqual(timeline.gaps[-1].reason, GapReason.WRITER_OVERFLOW)
        self.assertIsNone(timeline.gaps[-1].ended_at)

    async def test_guild_overflow_does_not_invalidate_another_guild(self) -> None:
        journal = VoiceJournal(self.analytics, queue_size=3)
        journal.start()
        for item in (
            record(0, VoiceSnapshot((human(),))),
            record(0, VoiceSnapshot((human(2, 20),)), guild=2, sequence=1),
            record(10, VoiceCheckpoint(), guild=2),
        ):
            self.assertEqual(journal.submit(item), Submission.ACCEPTED)
        self.assertEqual(
            journal.submit(record(20, VoiceObservation(human(1, None)))),
            Submission.FULL,
        )
        await journal.close()
        records = (
            *await self.store.read_all(1, START.date()),
            *await self.store.read_all(2, START.date()),
        )
        timeline = build_timeline(records)
        self.assertEqual(presence(timeline, 2).total_seconds, 10)
        self.assertEqual([gap.guild_id for gap in timeline.gaps], [1])
        self.assertEqual(timeline.gaps[0].started_at, at(20))
        self.assertEqual(await self.store.read_all(None, START.date()), ())

    async def test_overflow_keeps_independent_guild_and_global_markers(self) -> None:
        journal = VoiceJournal(self.analytics, queue_size=1)
        journal.start()
        journal.submit(record(0, VoiceCheckpoint(), guild=None))
        for item in (
            record(10, VoiceObservation(human()), guild=1),
            record(20, VoiceObservation(human(2)), guild=2),
            record(30, VoiceObservation(human()), guild=1),
            record(40, VoiceCheckpoint(), guild=None),
        ):
            self.assertEqual(journal.submit(item), Submission.FULL)
        await journal.close()
        for guild_id, start in ((1, 10), (2, 20), (None, 40)):
            with self.subTest(guild_id=guild_id):
                gaps = [
                    item.fact
                    for item in await self.store.read_all(guild_id, START.date())
                    if isinstance(item.fact, ObservationGap)
                ]
                self.assertEqual(len(gaps), 1)
                self.assertEqual(gaps[0].guild_id, guild_id)
                self.assertEqual(gaps[0].started_at, at(start))
                self.assertTrue(gaps[0].known_bounds)

    async def test_overflow_preserves_the_start_of_a_rejected_retrospective_gap(
        self,
    ) -> None:
        journal = VoiceJournal(self.analytics, queue_size=1)
        journal.start()
        journal.submit(record(0, VoiceSnapshot((human(),))))
        self.assertEqual(
            journal.submit(
                record(
                    20,
                    ObservationGap(
                        at(5),
                        None,
                        GapReason.CLOCK_DISCONTINUITY,
                        1,
                        known_bounds=False,
                    ),
                )
            ),
            Submission.FULL,
        )
        await journal.close()
        timeline = build_timeline(await self.store.read_all(1, START.date()))
        self.assertEqual(timeline.gaps[0].started_at, at(5))
        self.assertFalse(timeline.gaps[0].known_bounds)
        self.assertEqual(presence(timeline, 1).total_seconds, 5)

    async def test_later_overflow_preserves_an_imprecise_pending_loss_bound(
        self,
    ) -> None:
        journal = VoiceJournal(self.analytics, queue_size=1)
        journal.start()
        journal.submit(record(0, VoiceSnapshot((human(),))))
        for item in (
            record(
                20,
                ObservationGap(
                    at(5), None, GapReason.CLOCK_DISCONTINUITY, 1, known_bounds=False
                ),
            ),
            record(30, VoiceObservation(human(1, None))),
        ):
            self.assertEqual(journal.submit(item), Submission.FULL)
        await journal.close()
        gaps = [
            item.fact
            for item in await self.store.read_all(1, START.date())
            if isinstance(item.fact, ObservationGap)
        ]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].started_at, at(5))
        self.assertFalse(gaps[0].known_bounds)

    async def test_cancelled_close_retains_writer_until_physical_commit(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        append = self.analytics.append

        async def hold(token: str, records: Sequence[VoiceJournalRecord]) -> int:
            entered.set()
            await release.wait()
            return await append(token, records)

        with patch.object(self.analytics, "append", side_effect=hold):
            self.journal.start()
            self.journal.submit(record(0, VoiceSnapshot(())))
            await entered.wait()
            closing = asyncio.create_task(self.journal.close())
            scheduled = asyncio.Event()
            asyncio.get_running_loop().call_soon(scheduled.set)
            await scheduled.wait()
            closing.cancel()
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
        self.assertTrue(self.journal.closed)
        self.assertEqual(self.journal.counts.persisted, 1)
        self.assertEqual(len(await self.store.read_all(1)), 1)
