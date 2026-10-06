"""Retain checkpoint periods across resets without fabricating old uptime history."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, select

from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.schema import metadata, uptime_periods
from repositories.uptime_repository import UptimeCheckpoint, UptimeRepository
from utils.asyncio_utils import run_in_thread


class TestUptimeRepository(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "uptime.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.repository = UptimeRepository(self.database)

    async def test_resume_and_reset_preserve_previous_period(self) -> None:
        first = await self.repository.restore_or_start(
            now_us=100, threshold_us=1000, boot_id="a"
        )
        await self.repository.save(
            UptimeCheckpoint(first.period_id, 500, 400), boot_id="a"
        )
        resumed = await self.repository.restore_or_start(
            now_us=600, threshold_us=1000, boot_id="b"
        )
        self.assertEqual(resumed.period_id, first.period_id)
        self.assertEqual(resumed.accumulated_us, 400)
        with self.assertRaisesRegex(RuntimeError, "obsolete"):
            await self.repository.save(
                UptimeCheckpoint(first.period_id, 700, 600), boot_id="a"
            )
        await self.repository.save(
            UptimeCheckpoint(resumed.period_id, 800, 600), boot_id="b", final=True
        )
        reset = await self.repository.restore_or_start(
            now_us=2000, threshold_us=1000, boot_id="c"
        )
        self.assertNotEqual(reset.period_id, first.period_id)
        self.assertEqual(reset.accumulated_us, 0)
        async with self.database.transaction() as connection:
            periods = (
                await connection.execute(
                    select(
                        uptime_periods.c.period_id,
                        uptime_periods.c.accumulated_us,
                        uptime_periods.c.last_checkpoint_us,
                        uptime_periods.c.archived_us,
                        uptime_periods.c.reset_reason,
                    ).order_by(uptime_periods.c.period_id)
                )
            ).all()
        self.assertEqual(
            periods,
            [
                (first.period_id, 600, 800, 2000, "offline_threshold"),
                (reset.period_id, 0, 2000, None, None),
            ],
        )

    async def test_runtime_schema_matches_frozen_revision(self) -> None:
        def compare(connection: Connection) -> None:
            self.assertEqual(
                compare_metadata(MigrationContext.configure(connection), metadata), []
            )

        async with self.database.transaction() as connection:
            await connection.run_sync(compare)
