"""Exercise graceful and forced process termination with disposable SQLite state."""

import asyncio
import multiprocessing
import os
import signal
import sqlite3
import sys
import unittest
from contextlib import closing
from multiprocessing.connection import Connection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stupidbot import settings as config
from stupidbot.app import cli
from stupidbot.app.bot import StupidBot
from stupidbot.features.voice.facts import GapReason, VoiceCheckpoint, VoiceSnapshot
from stupidbot.features.voice.journal import Submission
from stupidbot.features.voice.metrics.presence import presence
from stupidbot.features.voice.repository import VoiceRepository
from stupidbot.storage.database import migrate
from tests.features.voice.fixtures import at, human, record


def _run_until_signal(database: str, ready: Connection) -> None:
    async def start(bot: StupidBot, *, token: str) -> None:
        if not token:
            raise AssertionError("The CLI did not pass its test credential")
        await bot.voice_analytics.append(
            "committed",
            [record(0, VoiceSnapshot((human(),))), record(10, VoiceCheckpoint())],
        )
        journal = bot.create_voice_journal()
        journal.start()
        # Hold the sole connection so the accepted record cannot commit before
        # the signal. Graceful cancellation releases it; process death cannot.
        async with bot._database.transaction():
            if journal.submit(record(20, VoiceCheckpoint())) is not Submission.ACCEPTED:
                raise AssertionError("The journal did not accept the pending record")
            ready.send(("ready", journal.pending))
            await asyncio.Event().wait()

    try:
        with (
            patch.object(sys, "argv", ["main.py", "--database", database]),
            patch.object(config, "DISCORD_BOT_TOKEN", "test"),
            patch.object(config, "DATA_DIR", Path(database).parent),
            patch.object(cli, "setup_logging"),
            patch.object(StupidBot, "start", start),
        ):
            cli.run()
    finally:
        ready.close()


@unittest.skipUnless(os.name == "posix", "Requires POSIX SIGTERM delivery")
class TestProcessShutdown(unittest.IsolatedAsyncioTestCase):
    def _stop_application(self, path: Path, *, force: bool) -> None:
        parent, child = multiprocessing.get_context("spawn").Pipe(duplex=False)
        process = multiprocessing.get_context("spawn").Process(
            target=_run_until_signal, args=(str(path), child)
        )
        try:
            process.start()
            child.close()
            self.assertTrue(parent.poll(10), "Application never became ready")
            self.assertEqual(parent.recv(), ("ready", 1))
            if process.pid is None:
                self.fail("Missing child process ID")
            if force:
                process.kill()
            else:
                os.kill(process.pid, signal.SIGTERM)
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "Application did not stop")
            if force:
                self.assertTrue(process.exitcode is not None and process.exitcode < 0)
            else:
                self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            parent.close()
            child.close()
            process.close()

    def test_service_stop_drains_voice_and_saves_final_uptime(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            migrate(path)
            self._stop_application(path, force=False)
            with closing(sqlite3.connect(path)) as reader:
                self.assertEqual(
                    reader.execute("SELECT origin FROM runtime_checkpoint").fetchone(),
                    ("shutdown",),
                )
                self.assertEqual(
                    reader.execute("SELECT count(*) FROM voice_records").fetchone(),
                    (3,),
                )

    async def test_forced_stop_restores_committed_voice_without_filling_restart_gap(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            await asyncio.to_thread(migrate, path)
            await asyncio.to_thread(self._stop_application, path, force=True)
            with closing(sqlite3.connect(path)) as reader:
                self.assertEqual(
                    reader.execute("SELECT origin FROM runtime_checkpoint").fetchone(),
                    ("startup",),
                )

            bot = StupidBot(database_path=path)
            try:
                await bot.restore_state()
                self.assertEqual(
                    await VoiceRepository(bot._database).read_all(1),
                    (
                        record(0, VoiceSnapshot((human(),))),
                        record(10, VoiceCheckpoint()),
                    ),
                )
                before = await bot.voice_analytics.snapshot(1)
                self.assertEqual(presence(before.timeline, 1).total_seconds, 10)
                await bot.voice_analytics.append(
                    "restarted",
                    [
                        record(100, VoiceSnapshot((human(),)), boot="two"),
                        record(110, VoiceCheckpoint(), boot="two"),
                    ],
                )
                await bot.voice_analytics.start()
                after = await bot.voice_analytics.snapshot(1)
                self.assertEqual(presence(after.timeline, 1).total_seconds, 20)
                self.assertEqual(
                    [
                        (gap.reason, gap.started_at, gap.ended_at)
                        for gap in after.timeline.gaps
                    ],
                    [(GapReason.PROCESS_RESTART, at(10), at(100))],
                )
            finally:
                await bot.close()
