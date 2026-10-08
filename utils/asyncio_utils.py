"""Preserve ownership of asynchronous and blocking work during cancellation."""

import asyncio
import contextvars
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


async def cancel_and_wait(task: asyncio.Task[object]) -> None:
    """Request task cancellation once and wait for its cleanup to finish.

    Concurrent callers do not interrupt cleanup with another cancel request.
    Caller cancellation also waits for completion before propagating. A task
    cannot join itself, so passing the current task is a no-op.

    Discard the target's result or exception, as gather(return_exceptions=True)
    does; its existing owner remains responsible for failure reporting.
    """
    if task is asyncio.current_task():
        return
    if not task.cancelling():
        task.cancel()
    completion = asyncio.gather(task, return_exceptions=True)
    cancellation: asyncio.CancelledError | None = None
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as error:
            cancellation = error
    if cancellation is not None:
        raise cancellation


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
