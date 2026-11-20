"""Advance the real event loop's timer queue without waiting for wall time."""

import asyncio
import datetime as wall_clock
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, tzinfo
from typing import Self, override
from unittest.mock import patch


class LoopClock:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.elapsed = 0.0
        self._loop = loop

    async def advance(self, seconds: float) -> None:
        self.elapsed += seconds
        reached = asyncio.Event()
        # Let timers whose deadlines have passed wake their tasks before returning.
        self._loop.call_later(0, reached.set)
        await reached.wait()


@contextmanager
def simulated_time() -> Generator[LoopClock]:
    loop = asyncio.get_running_loop()
    clock = LoopClock(loop)
    monotonic = loop.time
    wall_start = datetime.now(UTC).timestamp()

    class Clock(datetime):
        @classmethod
        @override
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(wall_start + clock.elapsed, tz)

    with (
        patch.object(wall_clock, "datetime", Clock),
        patch.object(loop, "time", new=lambda: monotonic() + clock.elapsed),
        patch.object(loop, "slow_callback_duration", float("inf")),
    ):
        yield clock
