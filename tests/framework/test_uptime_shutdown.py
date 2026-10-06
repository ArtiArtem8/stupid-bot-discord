"""Verify final uptime is committed before the shared database closes."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from api.voice.model import VoiceLifecycle, VoiceSnapshot
from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.schema import runtime_checkpoint
from repositories.voice_store import VoiceStore
from tests.api.voice.examples import record
from tests.storage import temporary_database
from utils.asyncio_utils import run_in_thread


class TestUptimeShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_close_waits_for_admitted_operation_and_saves_final_checkpoint(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            await run_in_thread(lambda: migrate(path))
            bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
            await bot.restore_state()
            entered = asyncio.Event()
            release = asyncio.Event()
            final_started = asyncio.Event()
            save = bot.uptime_manager.save_state

            async def final_save(*, final: bool = False) -> float:
                self.assertTrue(final)
                final_started.set()
                return await save(final=final)

            async def hold() -> None:
                async with bot._database.transaction():
                    entered.set()
                    await release.wait()

            holder = asyncio.create_task(hold())
            await entered.wait()
            with patch.object(bot.uptime_manager, "save_state", side_effect=final_save):
                closing = asyncio.create_task(bot.close())
                await final_started.wait()
                self.assertFalse(closing.done())
                closing.cancel()
                release.set()
                await holder
                with self.assertRaises(asyncio.CancelledError):
                    await closing
            await bot.close()
            with self.assertRaisesRegex(RuntimeError, "closing"):
                await bot.volume_repository.get_volume(1)
            reader = Database(open_engine(path))
            try:
                async with reader.transaction() as connection:
                    origin = await connection.scalar(
                        select(runtime_checkpoint.c.origin)
                    )
                self.assertEqual(origin, "shutdown")
            finally:
                await reader.close()

    async def test_shutdown_drains_accepted_voice_facts_before_final_uptime(
        self,
    ) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
        await bot.restore_state()
        journal = bot.create_voice_journal()
        journal.start()
        facts = [
            record(0, VoiceSnapshot(())),
            record(1, VoiceLifecycle(stopped=True), guild=None),
        ]
        for fact in facts:
            journal.submit(fact)
        await bot.close()
        reader = Database(open_engine(path))
        try:
            snapshot = await VoiceStore(reader).snapshot_for_guild(1)
            self.assertCountEqual(
                (*snapshot.guild_records, *snapshot.session_records), facts
            )
            async with reader.transaction() as connection:
                self.assertEqual(
                    await connection.scalar(select(runtime_checkpoint.c.origin)),
                    "shutdown",
                )
        finally:
            await reader.close()
