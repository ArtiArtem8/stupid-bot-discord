"""Await blocking operations without relinquishing ownership during cancellation."""

import asyncio
import contextvars
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


async def run_in_thread[T](operation: Callable[[], T]) -> T:
    """Run an operation in the default executor and await its physical completion.

    Copy the caller's context variables, as asyncio.to_thread does. Repeated
    caller cancellation waits for completion before propagating, so the caller
    can retain a resource lock around this await. The executor Future stays
    private and shielded; runner-wide Task cancellation cannot cancel it.

    Return the operation's result or propagate its exception. If cancellation
    was requested while the operation failed, log that failure and propagate
    cancellation instead. A blocking operation that never finishes also prevents
    its cancelled caller from finishing; there is no unsafe timeout fallback.
    """
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    work = loop.run_in_executor(None, context.run, operation)
    cancellation: asyncio.CancelledError | None = None
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError as error:
            cancellation = error
        except Exception:
            break
    try:
        result = work.result()
    except Exception:
        if cancellation is None:
            raise
        logger.exception("Worker operation failed while its caller was cancelled")
        raise cancellation from None
    if cancellation is not None:
        raise cancellation
    return result
