"""Maintenance preserves committed feature data and rejects unusable restores."""

import sqlite3
import unittest
from contextlib import closing
from functools import partial

from stupidbot.common.asyncio import run_in_thread
from stupidbot.features.music.repository import VolumeData, VolumeRepository
from stupidbot.features.voice.facts import (
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
)
from stupidbot.features.voice.repository import VoiceRepository
from stupidbot.storage.__main__ import Arguments, _run
from stupidbot.storage.database import Database, open_engine
from tests.features.voice.fixtures import human, record
from tests.storage.fixtures import temporary_database


class TestSQLiteMaintenance(unittest.IsolatedAsyncioTestCase):
    async def test_live_backup_restores_committed_settings_and_voice_history(
        self,
    ) -> None:
        path, database = await temporary_database(self)
        volumes = VolumeRepository(database)
        voice = VoiceRepository(database)
        await volumes.save(VolumeData(1, 0))
        await volumes.save(VolumeData(2, 75))
        facts = (
            record(0, VoiceLifecycle(), guild=None),
            record(1, VoiceSnapshot((human(1), human(2)))),
            record(90, VoiceObservation(human(1, channel_id=None))),
            record(120, VoiceSnapshot(())),
        )
        commit = await voice.append("before-backup", facts)
        history = await voice.history()
        self.assertGreater(path.with_name(f"{path.name}-wal").stat().st_size, 0)

        backup = Arguments()
        backup.command = "backup"
        backup.database = path
        backup.destination = path.with_name("backup.sqlite")
        await _run(backup)

        await volumes.save(VolumeData(1, 100))
        await voice.append("after-backup", [record(180, VoiceSnapshot((human(1),)))])
        await database.close()

        restore = Arguments()
        restore.command = "restore"
        restore.source = backup.destination
        restore.database = path.with_name("restored.sqlite")
        await _run(restore)
        restored = Database(open_engine(restore.database))
        self.addAsyncCleanup(restored.close)
        self.assertEqual(
            await VolumeRepository(restored).get_all(),
            [VolumeData(1, 0), VolumeData(2, 75)],
        )
        restored_voice = VoiceRepository(restored)
        self.assertEqual(await restored_voice.history(), history)
        self.assertEqual(await restored_voice.append("before-backup", facts), commit)
        self.assertEqual(await restored_voice.history(), history)

    async def test_restore_rejects_foreign_keys_and_unknown_revision(self) -> None:
        path, database = await temporary_database(self)
        await database.close()

        def corrupt(statement: str) -> None:
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(statement)
                connection.commit()

        for name, sql, error, message in (
            (
                "orphan",
                "INSERT INTO member_birthdays VALUES (999, 1, NULL, 0, 'N', 1)",
                ValueError,
                "foreign keys",
            ),
            (
                "revision",
                "DELETE FROM member_birthdays; "
                "UPDATE alembic_version SET version_num='unknown'",
                RuntimeError,
                "schema",
            ),
        ):
            with self.subTest(name=name):
                await run_in_thread(partial(corrupt, sql))
                args = Arguments()
                args.command = "restore"
                args.source = path
                args.database = path.with_name(f"{name}.sqlite")
                with self.assertRaisesRegex(error, message):
                    await _run(args)
