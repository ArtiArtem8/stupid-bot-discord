"""Exercise the opt-in repository against migrated on-disk SQLite databases."""

import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import override

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from experiments.birthday_sqlite.database import copy_database, migrate, open_engine
from experiments.birthday_sqlite.import_json import load_birthdays
from experiments.birthday_sqlite.repository import SQLiteBirthdayRepository
from experiments.birthday_sqlite.schema import congratulations, guilds, metadata, users
from repositories.birthday_repository import BirthdayRepository
from tests.repositories.fakes import InMemoryJsonStore
from utils.asyncio_utils import run_in_thread
from utils.json_types import JsonObject


class TestSQLiteBirthday(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pilot.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.engine = open_engine(self.path)
        self.addAsyncCleanup(self.engine.dispose)
        self.repo = SQLiteBirthdayRepository(self.engine)

    async def test_matches_json_repository_operations(self) -> None:
        reference = BirthdayRepository(InMemoryJsonStore())
        for repo in (reference, self.repo):
            self.assertIsNone(await repo.get(9))
            self.assertEqual(await repo.clear_user_birthday(9, 1), (False, False))
            await repo.configure_guild(1, "Original", 20, 30)
            await repo.set_user_birthday(1, "Ignored", 99, 2, "Member", "29-02-2000")
            self.assertTrue(await repo.record_congratulation(1, 2, date(2026, 2, 28)))
            self.assertFalse(await repo.record_congratulation(1, 2, date(2026, 2, 28)))
            self.assertFalse(await repo.record_congratulation(1, 99, date(2026, 2, 28)))
            await repo.set_user_birthday(1, "Ignored", 99, 2, "Renamed", "01-01-2000")
            self.assertEqual(await repo.clear_user_birthday(1, 99), (True, False))
            self.assertEqual(await repo.clear_user_birthday(1, 2), (True, True))
            self.assertEqual(await repo.clear_user_birthday(1, 2), (True, False))
            await repo.configure_guild(1, "Ignored", 40, None)
        self.assertEqual(await self.repo.get_all(), await reference.get_all())
        self.assertEqual(await self.repo.get_all_guild_ids(), [1])
        loaded = await self.repo.get(1)
        self.assertIsNotNone(loaded)
        if loaded is not None:
            loaded.users.clear()
        self.assertEqual(await self.repo.get(1), await reference.get(1))

    async def test_concurrent_settings_members_and_duplicate_markers(self) -> None:
        await self.repo.configure_guild(1, "Guild", 10, None)
        await asyncio.wait_for(
            asyncio.gather(
                self.repo.configure_guild(1, "Guild", 77, 88),
                *(
                    self.repo.set_user_birthday(
                        1, "Ignored", 99, uid, "Member", "01-01-2000"
                    )
                    for uid in range(30)
                ),
            ),
            timeout=10,
        )
        results = await asyncio.gather(
            *(
                self.repo.record_congratulation(1, 2, date(2026, 10, 6))
                for _ in range(15)
            )
        )
        self.assertEqual(sum(results), 1)
        loaded = await self.repo.get(1)
        if loaded is None:
            self.fail("Expected guild")
        self.assertEqual((loaded.channel_id, loaded.birthday_role_id), (77, 88))
        self.assertEqual(len(loaded.users), 30)
        self.assertEqual(loaded.users[2].was_congrats, ["06-10-2026"])

    async def test_save_override_history_replacement_and_cascade(self) -> None:
        original = BirthdayGuildConfig(1, "Guild", 20)
        original.users[2] = BirthdayUser(
            2, "Name", "", ["legacy", "legacy", "01-01-2020"]
        )
        await self.repo.save(original, key=3)
        original.guild_id = 3
        self.assertEqual(await self.repo.get(3), original)
        await self.repo.save(BirthdayGuildConfig(3, "Replacement", 40))
        self.assertEqual((await self.repo.get_all())[0].users, {})
        await self.repo.save(original)
        await self.repo.delete(3)
        self.assertIsNone(await self.repo.get(3))
        async with self.engine.begin() as connection:
            self.assertEqual(
                list((await connection.scalars(select(users.c.user_id))).all()), []
            )
            self.assertEqual(
                list((await connection.scalars(select(congratulations.c.value))).all()),
                [],
            )
            with self.assertRaises(IntegrityError):
                await connection.execute(
                    users.insert().values(
                        guild_id=999, user_id=2, name="N", birthday="", position=0
                    )
                )

    async def test_failed_aggregate_save_rolls_back_and_engine_is_reusable(
        self,
    ) -> None:
        original = BirthdayGuildConfig(1, "Original", 20)
        await self.repo.save(original)
        invalid = BirthdayGuildConfig(1, "Changed", 40)
        invalid.users[2**70] = BirthdayUser(2**70, "Too large", "")
        with self.assertRaises(OverflowError):
            await self.repo.save(invalid)
        self.assertEqual(await self.repo.get(1), original)
        await self.repo.configure_guild(2, "Still works", 30, None)
        self.assertEqual(await self.repo.get_all_guild_ids(), [1, 2])

    async def test_cancelled_transaction_cannot_commit_other_task(self) -> None:
        await self.repo.configure_guild(1, "Original", 20, None)
        entered, second_started = asyncio.Event(), asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with self.engine.begin() as connection:
                await connection.execute(
                    guilds.update().where(guilds.c.guild_id == 1).values(channel_id=999)
                )
                entered.set()
                await release.wait()

        async def second() -> None:
            second_started.set()
            await self.repo.configure_guild(2, "Independent", 30, None)

        first_task = asyncio.create_task(first())
        second_task: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second_task = asyncio.create_task(second())
            await asyncio.wait_for(second_started.wait(), 5)
            self.assertFalse(second_task.done())
            first_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(first_task, 5)
            await asyncio.wait_for(second_task, 5)
            configs = await self.repo.get_all()
            self.assertEqual(
                [(g.guild_id, g.channel_id) for g in configs], [(1, 20), (2, 30)]
            )
        finally:
            first_task.cancel()
            if second_task is not None:
                second_task.cancel()
            await asyncio.gather(
                first_task,
                *([second_task] if second_task else []),
                return_exceptions=True,
            )

    async def test_import_repeat_conflict_and_backup_restore(self) -> None:
        source = self.path.with_suffix(".json")
        config = BirthdayGuildConfig(1, "Guild", 20)
        config.users[2] = BirthdayUser(2, "Member", "29-02-2000", ["28-02-2025"])
        payload = json.dumps({"1": config.to_dict()}, ensure_ascii=False)
        await run_in_thread(lambda: source.write_text(payload, encoding="utf-8"))
        configs = await run_in_thread(lambda: load_birthdays(source))
        self.assertEqual(await self.repo.import_guilds(configs), 1)
        self.assertEqual(await self.repo.import_guilds(configs), 0)
        backup = self.path.with_name("backup.sqlite")
        restored = self.path.with_name("restored.sqlite")
        await run_in_thread(lambda: copy_database(self.path, backup))
        await self.repo.configure_guild(1, "Ignored", 99, None)
        with self.assertRaises(ValueError):
            await self.repo.import_guilds(
                [BirthdayGuildConfig(3, "Must roll back", 40), *configs]
            )
        self.assertIsNone(await self.repo.get(3))
        await run_in_thread(lambda: copy_database(backup, restored))
        restored_engine = open_engine(restored)
        try:
            self.assertEqual(
                await SQLiteBirthdayRepository(restored_engine).get_all(), configs
            )
        finally:
            await restored_engine.dispose()
        with self.assertRaises(FileExistsError):
            await run_in_thread(lambda: copy_database(self.path, backup))
        self.assertEqual(
            await run_in_thread(lambda: source.read_text(encoding="utf-8")), payload
        )

    async def test_cancelled_pool_waiter_never_writes(self) -> None:
        waiting = asyncio.Event()

        async def queued_write() -> None:
            waiting.set()
            await self.repo.configure_guild(2, "Cancelled", 30, None)

        async with self.engine.begin() as connection:
            await connection.execute(
                guilds.insert().values(
                    guild_id=1,
                    server_name="Owner",
                    channel_id=20,
                    birthday_role_id=None,
                )
            )
            task = asyncio.create_task(queued_write())
            try:
                await asyncio.wait_for(waiting.wait(), 5)
                self.assertFalse(task.done())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(await self.repo.get_all_guild_ids(), [1])
        await self.repo.configure_guild(3, "Reusable", 40, None)
        self.assertEqual(await self.repo.get_all_guild_ids(), [1, 3])

    async def test_migration_matches_runtime_schema(self) -> None:
        def compare(connection: Connection) -> None:
            self.assertEqual(
                compare_metadata(MigrationContext.configure(connection), metadata), []
            )

        async with self.engine.begin() as connection:
            await connection.run_sync(compare)
            self.assertEqual(
                (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar(), 1
            )
            self.assertEqual(
                (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar(),
                "wal",
            )
            self.assertEqual(
                (await connection.exec_driver_sql("PRAGMA synchronous")).scalar(), 2
            )

    async def test_migration_repeat_and_reopen_preserve_data(self) -> None:
        await self.repo.configure_guild(1, "Persisted", 20, None)
        await self.engine.dispose()
        await run_in_thread(lambda: migrate(self.path))
        reopened = open_engine(self.path)
        try:
            self.assertEqual(
                await SQLiteBirthdayRepository(reopened).get_all_guild_ids(), [1]
            )
        finally:
            await reopened.dispose()

    async def test_invalid_import_is_rejected_before_any_write(self) -> None:
        source = self.path.with_suffix(".json")
        invalid_cases: tuple[JsonObject, ...] = (
            {"1": {}},
            {
                "1": {
                    "Server_name": "G",
                    "Channel_id": "2",
                    "Users": {"3": {"name": "N", "birthday": "", "was_congrats": [42]}},
                }
            },
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                await run_in_thread(
                    lambda invalid=invalid: source.write_text(
                        json.dumps(invalid), encoding="utf-8"
                    )
                )
                with self.assertRaises(ValueError):
                    await run_in_thread(lambda: load_birthdays(source))
        self.assertEqual(await self.repo.get_all(), [])
