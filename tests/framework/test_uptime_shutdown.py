"""Verify final uptime is committed before the shared database closes."""

import asyncio
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from api.voice.model import VoiceLifecycle, VoiceSnapshot
from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.schema import runtime_checkpoint
from repositories.voice_repository import VoiceRepository
from tests.api.voice.examples import record
from tests.storage import temporary_database
from utils.asyncio_utils import run_in_thread


class TestUptimeShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_loop_continues_after_sqlite_busy(self) -> None:
        bot = object.__new__(StupidBot)
        saved = asyncio.Event()
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        attempts = 0

        async def save() -> float:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("checkpoint", None, busy)
            saved.set()
            return 1.0

        loop = bot.autosave_task
        loop.before_loop(AsyncMock())
        loop.change_interval(seconds=0.001)
        with patch.object(bot, "save_state", side_effect=save):
            task = loop.start()
            try:
                await asyncio.wait_for(saved.wait(), timeout=2.0)
                self.assertFalse(loop.failed())
            finally:
                loop.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertGreaterEqual(attempts, 2)

    async def test_storage_closes_after_voice_or_final_uptime_failure(self) -> None:
        for failing_step in ("voice", "uptime"):
            with self.subTest(failing_step=failing_step):
                path, reader = await temporary_database(self)
                bot = StupidBot(
                    database_path=path, cog_loader=MagicMock(spec=CogLoader)
                )
                await bot.restore_state()
                journal = bot.create_voice_journal()
                failure = RuntimeError(f"{failing_step} failure")
                with (
                    patch.object(
                        journal,
                        "close",
                        new=AsyncMock(
                            side_effect=failure if failing_step == "voice" else None
                        ),
                    ),
                    patch.object(
                        bot.uptime_manager,
                        "save_state",
                        new=AsyncMock(
                            wraps=bot.uptime_manager.save_state,
                            side_effect=failure if failing_step == "uptime" else None,
                        ),
                    ) as save,
                    self.assertRaises(RuntimeError) as raised,
                ):
                    await bot.close()
                self.assertIs(raised.exception, failure)
                save.assert_awaited_once_with(final=True)
                self.assertTrue(bot.is_closed())
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    await bot.volume_repository.get_volume(1)
                async with reader.transaction() as connection:
                    origin = await connection.scalar(
                        select(runtime_checkpoint.c.origin)
                    )
                self.assertEqual(
                    origin, "shutdown" if failing_step == "voice" else "startup"
                )

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
            history = await VoiceRepository(reader).history()
            self.assertEqual(history.records, tuple(facts))
            async with reader.transaction() as connection:
                self.assertEqual(
                    await connection.scalar(select(runtime_checkpoint.c.origin)),
                    "shutdown",
                )
        finally:
            await reader.close()

    async def test_presence_uses_the_same_elapsed_duration_as_persistence(self) -> None:
        path, _ = await temporary_database(self)
        bot = StupidBot(database_path=path, cog_loader=MagicMock(spec=CogLoader))
        self.addAsyncCleanup(bot.close)
        await bot.restore_state()
        with (
            patch.object(
                bot.uptime_manager, "elapsed_microseconds", return_value=120_000_000
            ) as elapsed,
            patch.object(bot, "change_presence", new_callable=AsyncMock) as presence,
        ):
            await bot.update_activity_task.coro(bot)
            self.assertEqual(await bot.save_state(), 120.0)
            self.assertEqual(elapsed.call_count, 2)
            presence.assert_awaited_once()
        async with bot._database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(runtime_checkpoint.c.accumulated_us)),
                120_000_000,
            )
