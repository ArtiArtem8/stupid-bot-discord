"""Exercise real POSIX service termination with disposable SQLite state."""

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
from stupidbot.features.voice.facts import VoiceCheckpoint
from stupidbot.storage.database import migrate
from tests.features.voice.fixtures import record


def _run_until_signal(database: str, ready: Connection) -> None:
    async def start(bot: StupidBot, *, token: str) -> None:
        if not token:
            raise AssertionError("The CLI did not pass its test credential")
        journal = bot.create_voice_journal()
        journal.start()
        journal.submit(record(0, VoiceCheckpoint()))
        ready.send("ready")
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
class TestSigtermShutdown(unittest.TestCase):
    def test_service_stop_drains_voice_and_saves_final_uptime(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            migrate(path)
            parent, child = multiprocessing.get_context("spawn").Pipe(duplex=False)
            process = multiprocessing.get_context("spawn").Process(
                target=_run_until_signal, args=(str(path), child)
            )
            try:
                process.start()
                child.close()
                self.assertTrue(parent.poll(10), "Application never became ready")
                self.assertEqual(parent.recv(), "ready")
                if process.pid is None:
                    self.fail("Missing child process ID")
                os.kill(process.pid, signal.SIGTERM)
                process.join(timeout=10)
                self.assertFalse(process.is_alive(), "SIGTERM cleanup did not finish")
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                parent.close()
                child.close()
                process.close()
            with closing(sqlite3.connect(path)) as reader:
                self.assertEqual(
                    reader.execute("SELECT origin FROM runtime_checkpoint").fetchone(),
                    ("shutdown",),
                )
                self.assertEqual(
                    reader.execute("SELECT count(*) FROM voice_records").fetchone(),
                    (1,),
                )
