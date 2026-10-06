"""Exercise uptime periods through the application owner and a temporary database."""

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import patch

from sqlalchemy import select

from framework.uptime_manager import UptimeManager
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.sqlite.schema import runtime_checkpoint, uptime_periods
from repositories.uptime_repository import UptimeRepository
from utils.asyncio_utils import run_in_thread


class TestUptimeManager(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "uptime.sqlite"
        await run_in_thread(lambda: migrate(self.path))
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.repository = UptimeRepository(self.database)

    async def test_restore_logs_start_resume_and_reset_after_commit(self) -> None:
        for moment, expected in ((100, "started"), (105, "resumed"), (1000, "reset")):
            with self.assertLogs(
                "repositories.uptime_repository", level="INFO"
            ) as logs:
                checkpoint = await self.repository.restore_or_start(
                    now_us=moment * 1_000_000,
                    threshold_us=60_000_000,
                    boot_id=str(moment),
                )
            self.assertIn(
                f"Uptime {expected}: period={checkpoint.period_id}", logs.output[0]
            )
            async with self.database.transaction() as connection:
                self.assertEqual(
                    await connection.scalar(select(runtime_checkpoint.c.period_id)),
                    checkpoint.period_id,
                )

    async def test_resume_then_reset_keeps_history_available_to_sql(self) -> None:
        first = UptimeManager(self.repository)
        with (
            patch.object(time, "time_ns", return_value=1000_000_000_000),
            patch.object(time, "monotonic_ns", return_value=0),
        ):
            await first.restore_uptime()
        with (
            patch.object(time, "time_ns", return_value=1120_000_000_000),
            patch.object(time, "monotonic_ns", return_value=120_000_000_000),
        ):
            self.assertEqual(await first.save_state(final=True), 120.0)
        second = UptimeManager(self.repository)
        with (
            patch.object(time, "time_ns", return_value=1130_000_000_000),
            patch.object(time, "monotonic_ns", return_value=0),
        ):
            await second.restore_uptime()
            self.assertEqual(second.elapsed_microseconds(), 120_000_000)
        with (
            patch.object(time, "time_ns", return_value=1140_000_000_000),
            patch.object(time, "monotonic_ns", return_value=10_000_000_000),
        ):
            self.assertEqual(await second.save_state(final=True), 130.0)
        third = UptimeManager(self.repository)
        with patch.object(time, "time_ns", return_value=6000_000_000_000):
            await third.restore_uptime()
            await third.restore_uptime()
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(
                        uptime_periods.c.accumulated_us,
                        uptime_periods.c.archived_us,
                        uptime_periods.c.reset_reason,
                    ).order_by(uptime_periods.c.period_id)
                )
            ).all()
            origin = await connection.scalar(select(runtime_checkpoint.c.origin))
        self.assertEqual(
            rows, [(130_000_000, 6000_000_000, "offline_threshold"), (0, None, None)]
        )
        self.assertEqual(origin, "startup")

    async def test_unrestored_owner_cannot_overwrite_checkpoint(self) -> None:
        manager = UptimeManager(self.repository)
        with self.assertRaisesRegex(RuntimeError, "not been restored"):
            await manager.save_state(final=True)
        async with self.database.transaction() as connection:
            self.assertEqual(
                (await connection.execute(select(runtime_checkpoint))).all(), []
            )

    async def test_closed_database_error_is_not_reported_as_success(self) -> None:
        manager = UptimeManager(self.repository)
        await manager.restore_uptime()
        await self.database.close()
        with self.assertRaisesRegex(RuntimeError, "closing"):
            await manager.save_state(final=True)

    async def test_wall_clock_jumps_do_not_change_elapsed_or_final_save(self) -> None:
        manager = UptimeManager(self.repository)
        with (
            patch.object(time, "time_ns", return_value=1000_000_000_000),
            patch.object(time, "monotonic_ns", return_value=10_000_000_000),
        ):
            await manager.restore_uptime()
        for wall, monotonic, expected in [(900, 30, 20), (9000, 50, 40)]:
            with (
                self.subTest(wall=wall),
                patch.object(time, "time_ns", return_value=wall * 1_000_000_000),
                patch.object(
                    time, "monotonic_ns", return_value=monotonic * 1_000_000_000
                ),
            ):
                self.assertEqual(manager.elapsed_microseconds(), expected * 1_000_000)
                self.assertEqual(await manager.save_state(final=True), expected)
            async with self.database.transaction() as connection:
                row = (
                    await connection.execute(
                        select(
                            runtime_checkpoint.c.checkpoint_us,
                            runtime_checkpoint.c.accumulated_us,
                            runtime_checkpoint.c.origin,
                        )
                    )
                ).one()
            self.assertEqual(row, (wall * 1_000_000, expected * 1_000_000, "shutdown"))
        resumed = UptimeManager(self.repository)
        with (
            patch.object(time, "time_ns", return_value=8000_000_000_000),
            patch.object(time, "monotonic_ns", return_value=0),
        ):
            await resumed.restore_uptime()
            self.assertEqual(resumed.elapsed_microseconds(), 40_000_000)
