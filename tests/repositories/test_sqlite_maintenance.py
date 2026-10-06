"""Maintenance rejects structurally valid but unusable restored databases."""

import sqlite3
import unittest
from contextlib import closing
from functools import partial

from repositories.sqlite.__main__ import Arguments, _run
from tests.storage import temporary_database
from utils.asyncio_utils import run_in_thread


class TestSQLiteMaintenance(unittest.IsolatedAsyncioTestCase):
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
