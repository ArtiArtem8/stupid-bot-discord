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

    async def test_tracing_is_opt_in_and_starts_before_bot_construction(self) -> None:
        for enabled in (False, True):
            with self.subTest(enabled=enabled), TemporaryDirectory() as directory:
                bot = MagicMock()
                bot.restore_state = AsyncMock()
                bot.start = AsyncMock()
                bot.save_state = AsyncMock(return_value=0.0)
                bot.__aenter__ = AsyncMock(return_value=bot)
                bot.__aexit__ = AsyncMock(return_value=False)
                args = ["main.py", "--tracemalloc"] if enabled else ["main.py"]
                root = Path(directory)
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
                        runtime: MagicMock = bot,
                        tracing_enabled: bool = enabled,
                    ) -> MagicMock:
                        self.assertEqual(trace.call_count, int(tracing_enabled))
                        self.assertFalse(watch_cogs)
                        return runtime

                    with patch.object(entry, "StupidBot", side_effect=create):
                        await entry.main()
                self.assertEqual(trace.call_count, int(enabled))
                bot.start.assert_awaited_once_with(token=credential)
                bot.save_state.assert_awaited_once()
