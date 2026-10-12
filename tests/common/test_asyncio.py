import asyncio
import contextvars
import unittest

from stupidbot.common.asyncio import cancel_and_wait, run_in_thread, wait_for_completion


class TestWaitForCompletion(unittest.IsolatedAsyncioTestCase):
    async def test_successful_and_already_completed_tasks_return_their_result(
        self,
    ) -> None:
        async def work() -> int:
            return 42

        task = asyncio.create_task(work())
        self.assertEqual(await wait_for_completion(task), 42)
        self.assertEqual(await wait_for_completion(task), 42)

    async def test_cancelled_waiter_drains_work_and_preserves_task_failure(
        self,
    ) -> None:
        async def work(release: asyncio.Event, failure: OSError | None) -> None:
            await release.wait()
            if failure is not None:
                raise failure

        async def wait(task: asyncio.Task[None], entered: asyncio.Event) -> None:
            entered.set()
            await wait_for_completion(task)

        for fail in (False, True):
            with self.subTest(fail=fail):
                entered, release = asyncio.Event(), asyncio.Event()
                failure = OSError("drain failed")

                task = asyncio.create_task(work(release, failure if fail else None))
                waiter = asyncio.create_task(wait(task, entered))
                await entered.wait()
                waiter.cancel()
                # The marker runs after the cancellation is delivered to the waiter.
                delivered = asyncio.Event()
                asyncio.get_running_loop().call_soon(delivered.set)
                await delivered.wait()
                self.assertFalse(waiter.done())
                self.assertEqual(task.cancelling(), 0)
                waiter.cancel()
                release.set()
                if fail:
                    with self.assertRaises(OSError) as raised:
                        await waiter
                    self.assertIs(raised.exception, failure)
                else:
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                self.assertTrue(task.done())

    async def test_independent_task_cancellation_propagates_without_retry(self) -> None:
        entered = asyncio.Event()

        async def work() -> None:
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(work())
        waiter = asyncio.create_task(wait_for_completion(task))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=1)
        self.assertTrue(task.cancelled())
        with self.assertRaises(asyncio.CancelledError):
            await wait_for_completion(task)


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
