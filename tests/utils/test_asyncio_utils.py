import asyncio
import contextvars
import unittest

from utils.asyncio_utils import cancel_and_wait, run_in_thread


class TestCancelAndWait(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_and_cancelled_waiter_preserve_cleanup(
        self,
    ) -> None:
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        second_entered = asyncio.Event()
        finished = False

        async def work() -> None:
            nonlocal finished
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished = True

        task = asyncio.create_task(work())
        await entered.wait()
        first = asyncio.create_task(cancel_and_wait(task))
        await cleaning.wait()

        async def cancel_again() -> None:
            second_entered.set()
            await cancel_and_wait(task)

        second = asyncio.create_task(cancel_again())
        await second_entered.wait()
        first.cancel()
        self.assertFalse(task.done())
        self.assertEqual(task.cancelling(), 1)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await second
        self.assertTrue(finished)
        self.assertTrue(task.cancelled())

    async def test_task_can_request_its_own_drain_without_deadlock(self) -> None:
        task = asyncio.current_task()
        if task is None:
            self.fail("Test must run in an asyncio task")
        await cancel_and_wait(task)
        self.assertEqual(task.cancelling(), 0)


class TestRunInThread(unittest.IsolatedAsyncioTestCase):
    async def test_worker_receives_context_without_changing_the_callers_context(
        self,
    ) -> None:
        value = contextvars.ContextVar("value", default="initial")
        token = value.set("caller")

        def operation() -> str:
            received = value.get()
            value.set("worker")
            return received

        try:
            self.assertEqual(await run_in_thread(operation), "caller")
            self.assertEqual(value.get(), "caller")
        finally:
            value.reset(token)
