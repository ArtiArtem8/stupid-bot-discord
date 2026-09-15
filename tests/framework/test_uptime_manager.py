"""Tests for asynchronous uptime persistence."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import patch

from framework.uptime_manager import UptimeData, UptimeManager
from utils.json_store import AsyncJsonFileStore


class TestUptimeData(unittest.TestCase):
    def test_empty_data_has_no_previous_run(self) -> None:
        self.assertIsNone(UptimeData.from_json({}))


class TestUptimeManager(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "uptime.json"
        self.store = AsyncJsonFileStore(self.path, backup_amount=0)

    async def test_restores_recent_accumulated_uptime(self) -> None:
        await self.store.write({"last_shutdown": 990.0, "accumulated_uptime": 120.0})

        with patch("framework.uptime_manager.time.time", return_value=1000.0):
            manager = UptimeManager(self.store)
            await manager.restore_uptime()

        self.assertEqual(manager.start_time, 880.0)

    async def test_corrupt_state_is_not_overwritten_on_shutdown(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        manager = UptimeManager(self.store)

        with self.assertLogs("framework.uptime_manager", level="ERROR"):
            await manager.restore_uptime()
        await manager.save_state()

        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")
