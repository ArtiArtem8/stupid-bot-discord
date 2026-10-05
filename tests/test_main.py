"""Allocation tracing is an explicit runtime choice, never an import side effect."""

import importlib
import sys
import tracemalloc
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import config
import main as entry


class TestMainTracing(unittest.IsolatedAsyncioTestCase):
    def test_import_does_not_start_tracing(self) -> None:
        with patch.object(tracemalloc, "start") as start:
            importlib.reload(entry)
        start.assert_not_called()

    async def test_runtime_switches_reach_bot_and_tracing_starts_first(self) -> None:
        for enabled, sqlite in ((False, False), (True, False), (False, True)):
            with (
                self.subTest(tracing=enabled, sqlite=sqlite),
                TemporaryDirectory() as directory,
            ):
                bot = MagicMock()
                bot.restore_state = AsyncMock()
                bot.start = AsyncMock()
                bot.save_state = AsyncMock(return_value=0.0)
                bot.__aenter__ = AsyncMock(return_value=bot)
                bot.__aexit__ = AsyncMock(return_value=False)
                args = ["main.py", "--tracemalloc"] if enabled else ["main.py"]
                root = Path(directory)
                selected = root / "birthdays.sqlite" if sqlite else None
                if selected is not None:
                    args.extend(("--birthday-sqlite", str(selected)))
                credential = "test"
                with (
                    patch.object(sys, "argv", args),
                    patch.object(config, "DISCORD_BOT_TOKEN", credential),
                    patch.object(config, "DATA_DIR", root / "data"),
                    patch.object(config, "BACKUP_DIR", root / "backups"),
                    patch.object(config, "COGS_DIR", root / "cogs"),
                    patch.object(entry, "setup_logging"),
                    patch.object(tracemalloc, "start") as trace,
                ):

                    def create(
                        *,
                        watch_cogs: bool,
                        birthday_database: Path | None,
                        runtime: MagicMock = bot,
                        tracing_enabled: bool = enabled,
                        expected_database: Path | None = selected,
                    ) -> MagicMock:
                        self.assertEqual(trace.call_count, int(tracing_enabled))
                        self.assertFalse(watch_cogs)
                        self.assertEqual(birthday_database, expected_database)
                        return runtime

                    with patch.object(entry, "StupidBot", side_effect=create):
                        await entry.main()
                self.assertEqual(trace.call_count, int(enabled))
                bot.start.assert_awaited_once_with(token=credential)
                bot.save_state.assert_awaited_once()
