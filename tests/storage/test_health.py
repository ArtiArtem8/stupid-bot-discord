"""Health counters observe transaction outcomes without changing their ownership."""

import sqlite3
import unittest

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from stupidbot.storage.database import Database
from tests.storage.fixtures import temporary_database


class TestDatabaseHealth(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_timeout_releases_admission_and_counts_one_failure(
        self,
    ) -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=AsyncAdaptedQueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.01,
        )
        database = Database(engine)
        self.addAsyncCleanup(database.close)
        async with database.transaction():
            self.assertEqual(database.health.admitted, 1)
            with self.assertRaises(PoolTimeoutError):
                async with database.transaction():
                    self.fail("The second checkout must exhaust the sole connection")
            self.assertEqual(database.health.admitted, 1)
            self.assertEqual(database.health.checkout_timeouts, 1)
        self.assertEqual(database.health.admitted, 0)
        await database.close()
        self.assertTrue(database.health.closing)

    async def test_only_busy_or_locked_errors_increment_contention(self) -> None:
        _, database = await temporary_database(self)
        for code, expected in (
            (sqlite3.SQLITE_BUSY, 1),
            (sqlite3.SQLITE_LOCKED | (1 << 8), 2),
            (sqlite3.SQLITE_CORRUPT, 2),
        ):
            with self.subTest(code=code):
                original = sqlite3.OperationalError("test failure")
                original.sqlite_errorcode = code
                error = OperationalError("test", None, original)
                with self.assertRaises(OperationalError) as raised:
                    async with database.transaction():
                        raise error
                self.assertIs(raised.exception, error)
                self.assertEqual(database.health.busy, expected)
                self.assertEqual(database.health.admitted, 0)
