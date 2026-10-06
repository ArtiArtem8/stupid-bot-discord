"""Runner-wide cancellation must not spin while closing owned application tasks."""

import asyncio
import faulthandler
import multiprocessing
import unittest
from unittest.mock import MagicMock

from framework.bot import StupidBot
from framework.cog_loader import CogLoader


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


def _run_shutdown_probe() -> None:
    faulthandler.dump_traceback_later(3)
    try:
        asyncio.run(_close_during_runner_shutdown())
    finally:
        faulthandler.cancel_dump_traceback_later()


class TestRunnerShutdown(unittest.TestCase):
    def test_close_does_not_spin_when_runner_cancels_owned_task(self) -> None:
        process = multiprocessing.get_context("spawn").Process(
            target=_run_shutdown_probe
        )
        try:
            process.start()
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "Application shutdown hung")
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            process.close()
