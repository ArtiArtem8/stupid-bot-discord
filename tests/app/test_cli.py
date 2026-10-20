"""Allocation tracing is an explicit runtime choice, never an import side effect."""

import asyncio
import importlib
import signal
import sys
import tracemalloc
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from stupidbot import settings as config
from stupidbot.app import cli as entry


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
                bot.close = AsyncMock()
                bot.__aenter__ = AsyncMock(return_value=bot)
                bot.__aexit__ = AsyncMock(return_value=False)
                args = ["main.py", "--tracemalloc"] if enabled else ["main.py"]
                root = Path(directory)
                selected = (
                    root / "selected.sqlite" if sqlite else root / "data" / "app.sqlite"
                )
                if sqlite:
                    args.extend(("--database", str(selected)))
                credential = "test"
                with (
                    patch.object(sys, "argv", args),
                    patch.object(config, "DISCORD_BOT_TOKEN", credential),
                    patch.object(config, "DATA_DIR", root / "data"),
                    patch.object(entry, "setup_logging"),
                    patch.object(tracemalloc, "start") as trace,
                ):

                    def create(
                        *,
                        watch_cogs: bool,
                        database_path: Path | None,
                        runtime: MagicMock = bot,
                        tracing_enabled: bool = enabled,
                        expected_database: Path | None = selected,
                    ) -> MagicMock:
                        self.assertEqual(trace.call_count, int(tracing_enabled))
                        self.assertFalse(watch_cogs)
                        self.assertEqual(database_path, expected_database)
                        return runtime

                    with patch.object(entry, "StupidBot", side_effect=create):
                        await entry.main()
                self.assertEqual(trace.call_count, int(enabled))
                bot.start.assert_awaited_once_with(token=credential)
                bot.save_state.assert_not_awaited()
                bot.close.assert_awaited_once()


class TestTerminationHandling(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_sigterm_preserves_cleanup_and_restores_handler(
        self,
    ) -> None:
        previous = signal.getsignal(signal.SIGTERM)
        running = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()

        async def application() -> None:
            with entry._handle_sigterm():
                try:
                    running.set()
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleaned.set()

        task = asyncio.create_task(application())
        await running.wait()
        handler = signal.getsignal(signal.SIGTERM)
        if not callable(handler):
            self.fail("Expected an application termination handler")
        try:
            handler(signal.SIGTERM, None)
            await asyncio.wait_for(cleaning.wait(), timeout=2)
            handler(signal.SIGTERM, None)
            self.assertFalse(task.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cleaned.is_set())
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    async def test_sigterm_does_not_hide_cancelled_final_cleanup(self) -> None:
        bot = MagicMock()
        bot.restore_state = AsyncMock()
        bot.close = AsyncMock(side_effect=asyncio.CancelledError)
        bot.__aenter__ = AsyncMock(return_value=bot)
        bot.__aexit__ = AsyncMock(return_value=False)

        async def terminate(*, token: str) -> None:
            self.assertEqual(token, "test")
            handler = signal.getsignal(signal.SIGTERM)
            if not callable(handler):
                self.fail("Expected an application termination handler")
            handler(signal.SIGTERM, None)
            await asyncio.Event().wait()

        bot.start = AsyncMock(side_effect=terminate)
        with (
            TemporaryDirectory() as directory,
            patch.object(sys, "argv", ["main.py"]),
            patch.object(config, "DISCORD_BOT_TOKEN", "test"),
            patch.object(config, "DATA_DIR", Path(directory)),
            patch.object(entry, "setup_logging"),
            patch.object(entry, "StupidBot", return_value=bot),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.create_task(entry.main())
        bot.close.assert_awaited_once()

    async def test_unrelated_cancellation_propagates_and_restores_handler(self) -> None:
        previous = signal.getsignal(signal.SIGTERM)
        running = asyncio.Event()

        async def application() -> None:
            with entry._handle_sigterm():
                running.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(application())
        await running.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
