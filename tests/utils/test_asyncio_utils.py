import contextvars
import unittest

from utils.asyncio_utils import run_in_thread


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
