"""Process death and commit cancellation retain resolvable voice batch identity."""

import asyncio
import multiprocessing
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import event

from api.voice.model import VoiceSnapshot
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.identity import ensure_guild
from repositories.sqlite.schema import music_settings
from repositories.voice_repository import VoiceRepository
from tests.api.voice.examples import record
from tests.storage import temporary_database


def _crash_writer(path: str) -> None:
    async def write() -> None:
        database = Database(open_engine(Path(path)))
        await VoiceRepository(database).append(
            "committed", [record(0, VoiceSnapshot(()))]
        )
        async with database.transaction() as connection:
            await ensure_guild(connection, 2)
            await connection.execute(
                music_settings.insert().values(guild_id=2, volume=50, version=1)
            )
            os._exit(23)

    asyncio.run(write())


class TestStorageCrash(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_at_commit_resolves_by_batch_id_and_connection_remains_usable(
        self,
    ) -> None:
        _, database = await temporary_database(self)
        store = VoiceRepository(database)
        task = asyncio.current_task()
        if task is None:
            self.fail("Missing test task")

        def cancel_at_commit(_connection: object) -> None:
            asyncio.get_running_loop().call_soon(task.cancel)

        facts = [record(0, VoiceSnapshot(()))]
        event.listen(database.engine.sync_engine, "commit", cancel_at_commit, once=True)
        with self.assertRaises(asyncio.CancelledError):
            await store.append("uncertain", facts)
        revision = await store.append("uncertain", facts)
        self.assertEqual(await store.append("uncertain", facts), revision)
        self.assertEqual(await store.read_all(1), tuple(facts))

    async def test_process_death_retains_commit_and_discards_uncommitted_work(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "crash.sqlite"
            await asyncio.to_thread(migrate, path)
            process = multiprocessing.get_context("spawn").Process(
                target=_crash_writer, args=(str(path),)
            )
            try:
                process.start()
                await asyncio.to_thread(process.join, 15)
                self.assertFalse(process.is_alive(), "Crash probe did not finish")
                self.assertEqual(process.exitcode, 23)
            finally:
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 5)
                process.close()
            database = Database(open_engine(path))
            try:
                store = VoiceRepository(database)
                facts = [record(0, VoiceSnapshot(()))]
                self.assertEqual(await store.read_all(1), tuple(facts))
                await store.append("committed", facts)
                async with database.transaction() as connection:
                    self.assertEqual(
                        (await connection.execute(music_settings.select())).all(), []
                    )
            finally:
                await database.close()
