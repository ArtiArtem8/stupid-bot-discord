"""Verify migration continuity, volume parity and explicit conflict-safe import."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from sqlalchemy.exc import IntegrityError

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.birthday_sqlite.repository import SQLiteBirthdayRepository
from repositories.sqlite.__main__ import Arguments, _run
from repositories.sqlite.database import (
    Database,
    copy_database,
    migrate,
    open_engine,
    validate_schema,
)
from repositories.sqlite.import_json import load_volumes
from repositories.sqlite_volume_repository import SQLiteVolumeRepository
from repositories.volume_repository import VolumeData, VolumeRepository
from tests.repositories.fakes import InMemoryJsonStore
from utils.asyncio_utils import run_in_thread


class TestSQLiteVolume(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "shared.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.repo = SQLiteVolumeRepository(self.database)

    async def test_matches_json_and_preserves_other_guilds(self) -> None:
        reference = VolumeRepository(InMemoryJsonStore())
        default = await reference.get_volume(1)
        for repository in (reference, self.repo):
            self.assertIsNone(await repository.get(1))
            self.assertEqual(await repository.get_volume(1), default)
            await repository.save(VolumeData(1, 0), key=999)
            await repository.save(VolumeData(2, 200))
            await repository.save(VolumeData(1, 37))
            await repository.save(VolumeData(3, 0))
            self.assertEqual(await repository.get_volume(3), 0)
            await repository.delete(2)
            await repository.delete(2)
        self.assertEqual(await self.repo.get_all(), await reference.get_all())

    async def test_concurrent_writes_and_failure_leave_connection_usable(self) -> None:
        birthdays = SQLiteBirthdayRepository(self.database)
        await asyncio.gather(
            self.repo.save(VolumeData(1, 12)),
            self.repo.save(VolumeData(2, 18)),
            birthdays.configure_guild(1, "Guild", 4, None),
        )
        with self.assertRaises(IntegrityError):
            async with self.database.transaction() as connection:
                await connection.exec_driver_sql(
                    "INSERT INTO music_volumes VALUES (3, NULL)"
                )
        self.assertIsNone(await self.repo.get(3))
        self.assertEqual(
            await self.repo.get_all(), [VolumeData(1, 12), VolumeData(2, 18)]
        )
        self.assertEqual(await birthdays.get_all_guild_ids(), [1])

    async def test_upgrade_0001_preserves_birthdays_and_backup_contains_both(
        self,
    ) -> None:
        old_path = self.path.with_name("existing.sqlite")
        await run_in_thread(lambda: migrate(old_path, "0001_birthdays"))
        old_database = Database(open_engine(old_path))
        guild = BirthdayGuildConfig(1, "Existing", 2)
        guild.users[3] = BirthdayUser(3, "Member", "01-01-2000", ["legacy", "legacy"])
        try:
            await SQLiteBirthdayRepository(old_database).save(guild)
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                await validate_schema(old_database.engine)
        finally:
            await old_database.close()
        await run_in_thread(lambda: migrate(old_path))
        upgraded = Database(open_engine(old_path))
        try:
            await validate_schema(upgraded.engine)
            self.assertEqual(await SQLiteBirthdayRepository(upgraded).get(1), guild)
            await SQLiteVolumeRepository(upgraded).save(VolumeData(99, 42))
            backup = self.path.with_name("backup.sqlite")
            await run_in_thread(lambda: copy_database(old_path, backup))
        finally:
            await upgraded.close()
        restored = Database(open_engine(backup))
        try:
            self.assertEqual(await SQLiteBirthdayRepository(restored).get(1), guild)
            self.assertEqual(await SQLiteVolumeRepository(restored).get_volume(99), 42)
        finally:
            await restored.close()

    async def test_cli_import_is_idempotent_atomic_and_keeps_source(self) -> None:
        source = self.path.with_suffix(".json")
        original = b'{"1":0,"2":"37","3":200.0}'
        await run_in_thread(lambda: source.write_bytes(original))
        args = Arguments()
        args.command = "import-volumes"
        args.database = self.path
        args.source = source
        await _run(args)
        await _run(args)
        self.assertEqual(
            await self.repo.get_all(),
            [VolumeData(1, 0), VolumeData(2, 37), VolumeData(3, 200)],
        )
        self.assertEqual(await run_in_thread(source.read_bytes), original)
        await run_in_thread(lambda: source.write_bytes(b'{"4":55,"2":99}'))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            await _run(args)
        self.assertIsNone(await self.repo.get(4))
        self.assertEqual(await self.repo.get_volume(2), 37)

    async def test_ambiguous_inputs_fail_before_writing(self) -> None:
        source = self.path.with_suffix(".json")
        for content in (
            '{"1":20,"1":30}',
            '{"1":20,"01":20}',
            '{"1":true}',
            '{"1":42.5}',
            '{"1":null}',
            '{"x":2}',
            '{"1":{}}',
            '{"9223372036854775808":10}',
            '{"1":9223372036854775808}',
        ):
            with self.subTest(content=content):
                await run_in_thread(lambda content=content: source.write_text(content))
                with self.assertRaises(ValueError):
                    await run_in_thread(lambda: load_volumes(source))
        self.assertEqual(await self.repo.get_all(), [])
