import asyncio
import json
import unittest
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import patch

from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceJournalRecord,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.timeline import build_timeline
from repositories._voice_codec import decode_record, encode_record
from repositories.voice_journal import JournalWriteError, Submission, VoiceJournal
from tests.api.voice.examples import START, at, human, record


class TestVoiceCodec(unittest.TestCase):
    def test_v2_round_trip_preserves_full_state_and_unknown_fields(self) -> None:
        state = VoiceStateSnapshot(
            1,
            10,
            False,
            self_mute=True,
            self_deaf=False,
            server_mute=True,
            server_deaf=False,
            self_stream=True,
            self_video=True,
            suppress=False,
            requested_to_speak_at=at(2),
            session_id="session",
            afk=False,
        )
        for fact in (
            VoiceObservation(state),
            VoiceSnapshot((state,)),
            VoiceSnapshot((state,), authoritative=False),
            ObservationGap(at(0), at(10), GapReason.WRITE_FAILURE, 1),
            VoiceCheckpoint(),
        ):
            with self.subTest(fact=fact):
                item = record(10, fact)
                self.assertEqual(decode_record(encode_record(item)), item)
        encoded = json.loads(encode_record(record(10, VoiceObservation(state))))
        self.assertEqual(encoded["schema_version"], 2)
        self.assertIn("self_stream", encoded["state"])
        self.assertNotIn("sm", encoded["state"])

    def test_legacy_snapshot_does_not_invent_missing_flags(self) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "startup",
                    "guild": 1,
                    "detail": {"presence": {"10": [1]}, "bot_presence": {"10": [9]}},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceSnapshot)
        if isinstance(item.fact, VoiceSnapshot):
            human_state, bot = item.fact.states
            self.assertIsNone(human_state.self_mute)
            self.assertIsNone(human_state.session_id)
            self.assertIsNone(human_state.requested_to_speak_at)
            self.assertIs(human_state.is_bot, False)
            self.assertIs(bot.is_bot, True)

    def test_legacy_flags_are_read_but_raised_hand_cannot_invent_timestamp(
        self,
    ) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "flags",
                    "guild": 1,
                    "user": 1,
                    "channel_after": 10,
                    "flags_after": {"sm": True, "sd": False, "st": True, "hr": True},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceObservation)
        if isinstance(item.fact, VoiceObservation):
            self.assertIs(item.fact.state.self_mute, True)
            self.assertIs(item.fact.state.self_deaf, False)
            self.assertIs(item.fact.state.self_stream, True)
            self.assertIsNone(item.fact.state.self_video)
            self.assertIsNone(item.fact.state.requested_to_speak_at)

    def test_legacy_heartbeat_without_maps_cannot_close_gap(self) -> None:
        legacy = decode_record(
            json.dumps(
                {
                    "seq": 300,
                    "boot": "one",
                    "at": at(30).isoformat(),
                    "mono": 30,
                    "kind": "heartbeat",
                    "guild": 1,
                    "detail": {"members": 1},
                }
            )
        )
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(
                    10,
                    ObservationGap(at(10), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                legacy,
                record(40, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)

    def test_unknown_version_and_corrupt_schema_fail_explicitly(self) -> None:
        for line in ('{"schema_version": 3}', "not json", '{"schema_version": 2}'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                decode_record(line)

    def test_partial_legacy_snapshot_is_not_claimed_as_a_full_population(self) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "startup",
                    "guild": 1,
                    "detail": {"presence": {"10": [1]}},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceSnapshot)
        if isinstance(item.fact, VoiceSnapshot):
            self.assertFalse(item.fact.authoritative)
            self.assertEqual(item.fact.states[0].user_id, 1)

    def test_legacy_overflow_after_a_snapshot_does_not_confirm_the_lost_window(
        self,
    ) -> None:
        overflow = decode_record(
            json.dumps(
                {
                    "seq": 301,
                    "boot": "one",
                    "at": at(30).isoformat(),
                    "mono": 30,
                    "kind": "overflow",
                    "detail": {"dropped": 2},
                }
            )
        )
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(20, VoiceCheckpoint()),
                record(30, VoiceSnapshot((human(),))),
                overflow,
                record(40, VoiceSnapshot((human(),))),
                record(50, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)


class TestVoiceJournal(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.journal = VoiceJournal(self.root, batch_size=2)
        self.addCleanup(self.directory.cleanup)

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
        self.assertEqual(len(await self.journal.read_day(1, START.date())), 5)
        self.assertEqual(
            self.journal.submit(record(6, VoiceCheckpoint())), Submission.CLOSED
        )
        await self.journal.close()

    async def test_close_awaits_in_flight_thread_even_if_caller_is_cancelled(
        self,
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        append = self.journal._append

        def blocked(records: Sequence[VoiceJournalRecord]) -> None:
            loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=10)
            append(records)

        with patch.object(self.journal, "_append", side_effect=blocked):
            self.journal.start()
            self.journal.submit(record(0, VoiceSnapshot((human(),))))
            await asyncio.wait_for(started.wait(), 5)
            closing = asyncio.create_task(self.journal.close())
            # The close operation starts before the writer is released.
            closing_started = asyncio.Event()
            loop.call_soon(closing_started.set)
            await closing_started.wait()
            self.assertFalse(closing.done())
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            release.set()
            await self.journal.close()
        self.assertEqual(self.journal.counts.persisted, 1)
        self.assertEqual(len(await self.journal.read_day(1, START.date())), 1)

    async def test_write_failure_never_reports_persistence(self) -> None:
        with patch.object(
            self.journal, "_append", side_effect=OSError("disk unavailable")
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
        append = self.journal._append
        calls = 0

        def fail_once(records: Sequence[VoiceJournalRecord]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient write failure")
            append(records)

        with patch.object(self.journal, "_append", side_effect=fail_once):
            self.journal.start()
            self.journal.submit(record(0, VoiceSnapshot((human(),))))
            with self.assertLogs("repositories.voice_journal", level="WARNING"):
                with self.assertRaises(JournalWriteError):
                    await self.journal.close()
        gaps = await self.journal.read_day(None, START.date())
        self.assertEqual(len(gaps), 1)
        self.assertIsInstance(gaps[0].fact, ObservationGap)
        if isinstance(gaps[0].fact, ObservationGap):
            self.assertEqual(gaps[0].fact.reason, GapReason.WRITE_FAILURE)
        self.assertEqual(await self.journal.read_day(1, START.date()), ())

    async def test_bounded_queue_records_overflow_gap(self) -> None:
        journal = VoiceJournal(self.root, queue_size=1)
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
            *await journal.read_day(1, START.date()),
            *await journal.read_day(None, START.date()),
        )
        timeline = build_timeline(records)
        self.assertEqual(timeline.gaps[-1].reason, GapReason.WRITER_OVERFLOW)
        self.assertIsNone(timeline.gaps[-1].ended_at)

    async def test_rotation_compression_and_retention_leave_legacy_bytes_untouched(
        self,
    ) -> None:
        legacy = self.root / "guild_1" / f"events_{START.date()}.jsonl"
        legacy.parent.mkdir(parents=True)
        legacy_text = (
            json.dumps(
                {
                    "seq": 1,
                    "boot": "legacy",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "startup",
                    "guild": 1,
                    "detail": {"presence": {"10": [2]}},
                }
            )
            + "\n"
        )
        legacy.write_text(legacy_text, encoding="utf-8")
        before = legacy.read_bytes()
        self.journal.start()
        self.journal.submit(record(0, VoiceSnapshot((human(),))))
        self.journal.submit(record(86400, VoiceCheckpoint()))
        await self.journal.close()
        self.assertEqual(len(await self.journal.read_day(1, START.date())), 2)
        self.assertEqual(
            await self.journal.compact(before_day=START.date() + timedelta(days=1)), 1
        )
        self.assertEqual(len(await self.journal.read_day(1, START.date())), 2)
        self.assertTrue(
            (self.root / "v2/guild_1" / f"events_{START.date()}.jsonl.gz").exists()
        )
        self.assertEqual(
            await self.journal.prune(
                today=START.date() + timedelta(days=10), retention_days=1
            ),
            2,
        )
        self.assertEqual(legacy.read_bytes(), before)

    async def test_reader_does_not_hide_corrupt_line_as_continuous_presence(
        self,
    ) -> None:
        path = self.root / "v2/guild_1" / f"events_{START.date()}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("truncated", encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.journal.read_day(1, START.date())

    async def test_rotation_uses_utc_calendar_day(self) -> None:
        from datetime import timezone

        item = replace(
            record(0, VoiceSnapshot((human(),))),
            observed_at=START.astimezone(timezone(timedelta(hours=-7))),
        )
        self.journal.start()
        self.journal.submit(item)
        await self.journal.close()
        self.assertEqual(await self.journal.read_day(1, START.date()), (item,))

    async def test_partial_failed_batch_cannot_credit_presence_before_recovery(
        self,
    ) -> None:
        append = self.journal._append
        first = True

        def partial_failure(records: Sequence[VoiceJournalRecord]) -> None:
            nonlocal first
            if first:
                first = False
                append(records[:1])
                raise OSError("Failure after a partial append")
            append(records)

        with patch.object(self.journal, "_append", side_effect=partial_failure):
            self.journal.start()
            for item in (
                record(0, VoiceSnapshot((human(),))),
                record(10, VoiceCheckpoint()),
                record(20, VoiceSnapshot((human(),))),
                record(30, VoiceCheckpoint()),
            ):
                self.journal.submit(item)
            with self.assertLogs("repositories.voice_journal", level="WARNING"):
                with self.assertRaises(JournalWriteError):
                    await self.journal.close()
        timeline = build_timeline(
            (
                *await self.journal.read_day(1, START.date()),
                *await self.journal.read_day(None, START.date()),
            )
        )
        self.assertEqual(self.journal.counts.failed, 2)
        self.assertEqual(self.journal.counts.persisted, 2)
        self.assertEqual(presence(timeline, 1).total_seconds, 10)
