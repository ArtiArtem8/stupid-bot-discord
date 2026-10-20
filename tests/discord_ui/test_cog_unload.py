"""Cog cleanup preserves cancellation across discord.py's unload error handler."""

import asyncio
import unittest
from unittest.mock import MagicMock

from discord.ext import commands

from stupidbot.discord_ui.base_cog import BaseCog


class TestCogUnload(unittest.IsolatedAsyncioTestCase):
    async def test_uncancelled_failure_propagates_without_duplicate_logging(
        self,
    ) -> None:
        cog = BaseCog(MagicMock(spec=commands.Bot))
        failure = OSError("cleanup failed")

        async def fail() -> None:
            raise failure

        for already_done in (False, True):
            with self.subTest(already_done=already_done):
                task = asyncio.create_task(fail())
                if already_done:
                    await asyncio.gather(task, return_exceptions=True)
                with (
                    self.assertNoLogs("stupidbot.discord_ui.base_cog"),
                    self.assertRaises(OSError) as raised,
                ):
                    await cog._wait_for_unload(task)
                self.assertIs(raised.exception, failure)

    async def test_repeated_cancellation_drains_cleanup_then_logs_failure(self) -> None:
        cog = BaseCog(MagicMock(spec=commands.Bot))
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        failure = BrokenPipeError("renderer pipe closed")

        async def cleanup() -> None:
            await release.wait()
            finished.set()
            raise failure

        async def unload(task: asyncio.Task[None]) -> None:
            entered.set()
            await cog._wait_for_unload(task)

        task = asyncio.create_task(cleanup())
        waiter = asyncio.create_task(unload(task))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with self.assertLogs(
                "stupidbot.discord_ui.base_cog", level="ERROR"
            ) as logs:
                for _ in range(2):
                    waiter.cancel()
                    delivered = asyncio.Event()
                    asyncio.get_running_loop().call_soon(delivered.set)
                    await delivered.wait()
                    self.assertFalse(waiter.done())
                    self.assertFalse(finished.is_set())
                    self.assertEqual(task.cancelling(), 0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(waiter, 1)
            self.assertTrue(finished.is_set())
            self.assertTrue(task.done())
            self.assertEqual(len(logs.records), 1)
            self.assertIn(cog.qualified_name, logs.records[0].getMessage())
            info = logs.records[0].exc_info
            if info is None:
                self.fail("Expected the cleanup failure traceback")
            self.assertIs(info[1], failure)
        finally:
            release.set()
            await asyncio.wait_for(
                asyncio.gather(task, waiter, return_exceptions=True), 1
            )

    async def test_independent_cleanup_cancellation_propagates(self) -> None:
        cog = BaseCog(MagicMock(spec=commands.Bot))
        entered = asyncio.Event()

        async def cleanup() -> None:
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(cleanup())
        waiter = asyncio.create_task(cog._wait_for_unload(task))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertNoLogs("stupidbot.discord_ui.base_cog"):
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, 1)
            with self.assertRaises(asyncio.CancelledError):
                await cog._wait_for_unload(task)
        self.assertTrue(task.cancelled())
