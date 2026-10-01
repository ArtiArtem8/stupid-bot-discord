"""Exercise runner-wide cancellation in child processes with an external deadline."""

import asyncio
import faulthandler
import multiprocessing
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from unittest.mock import MagicMock, patch

from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from utils.json_store import AsyncJsonFileStore
from utils.json_utils import get_json, save_json

type Scenario = Literal["json", "json-worker-cancel", "bot"]


async def _write_during_runner_shutdown(path: Path, *, worker_cancelled: bool) -> None:
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocked_save(*_args: object, **_kwargs: object) -> None:
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("Runner did not release the physical writer")
        if worker_cancelled:
            raise asyncio.CancelledError
        save_json(path, {"completed": True}, backup_amount=0)

    async def release_on_shutdown() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            release.set()

    # Runner cancellation includes every Task, including any to_thread wrapper.
    releaser = asyncio.create_task(release_on_shutdown())
    store = AsyncJsonFileStore(path, backup_amount=0)
    with patch("utils.json_store.save_json", side_effect=blocked_save):
        writer = asyncio.create_task(store.write({"completed": True}))
        await started.wait()
    if writer.done() or releaser.done():
        raise AssertionError("The runner must encounter pending tasks")


async def _close_during_runner_shutdown() -> None:
    started = asyncio.Event()

    async def blocked_close() -> None:
        started.set()
        await asyncio.Event().wait()

    loader = MagicMock(spec=CogLoader)
    loader.close.side_effect = blocked_close
    bot = StupidBot(cog_loader=loader)
    closing = asyncio.create_task(bot.close())
    await started.wait()
    if closing.done():
        raise AssertionError("The runner must encounter a pending close")


def _run_shutdown_probe(scenario: Scenario, root: str) -> None:
    faulthandler.dump_traceback_later(2)
    try:
        if scenario != "bot":
            path = Path(root) / "state.json"
            asyncio.run(
                _write_during_runner_shutdown(
                    path, worker_cancelled=scenario == "json-worker-cancel"
                )
            )
            if scenario == "json" and get_json(path) != {"completed": True}:
                raise AssertionError("Shutdown returned before the write completed")
        else:
            asyncio.run(_close_during_runner_shutdown())
    finally:
        faulthandler.cancel_dump_traceback_later()


class TestRunnerShutdown(unittest.TestCase):
    def _check_shutdown(self, scenario: Scenario) -> None:
        with TemporaryDirectory() as root:
            process = multiprocessing.get_context("spawn").Process(
                target=_run_shutdown_probe, args=(scenario, root)
            )
            try:
                process.start()
                process.join(timeout=10)
                self.assertFalse(process.is_alive(), f"{scenario} shutdown hung")
                self.assertEqual(process.exitcode, 0, f"{scenario} probe failed")
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                process.close()

    def test_json_write_finishes_when_runner_cancels_all_tasks(self) -> None:
        self._check_shutdown("json")

    def test_close_does_not_spin_when_runner_cancels_owned_task(self) -> None:
        self._check_shutdown("bot")

    def test_cancelled_worker_does_not_spin_during_runner_shutdown(self) -> None:
        self._check_shutdown("json-worker-cancel")
