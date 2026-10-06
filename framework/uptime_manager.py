"""Measure the current uptime period and persist its confirmed checkpoints."""

import asyncio
import time
from uuid import uuid4

import config
from repositories.uptime_repository import UptimeCheckpoint, UptimeRepository


class UptimeManager:
    """Own one process's uptime, resuming only a successfully restored period.

    The repository archives periods on reset. Saves serialize with restoration
    so an older sampled checkpoint cannot overwrite a newer one. Persistence
    failures propagate; shutdown must save before closing the database.
    """

    def __init__(self, repository: UptimeRepository) -> None:
        self.last_activity_str = "N/A"
        self._repository = repository
        self._boot_id = uuid4().hex
        self._checkpoint: UptimeCheckpoint | None = None
        self._restored_us = 0
        self._monotonic_origin_ns = 0
        self._lock = asyncio.Lock()

    async def restore_uptime(self) -> None:
        """Resume or start a period after schema validation, once per process."""
        async with self._lock:
            if self._checkpoint is not None:
                return
            now_us = time.time_ns() // 1000
            checkpoint = await self._repository.restore_or_start(
                now_us=now_us,
                threshold_us=config.DISCONNECT_TIMER_THRESHOLD * 1_000_000,
                boot_id=self._boot_id,
            )
            self._checkpoint = checkpoint
            self._restored_us = checkpoint.accumulated_us
            self._monotonic_origin_ns = time.monotonic_ns()

    def elapsed_microseconds(self) -> int:
        """Return restored uptime plus this process's monotonic elapsed time.

        Wall time only dates checkpoints and determines restart/reset policy.
        The monotonic origin is never persisted or reused by another process.
        """
        if self._checkpoint is None:
            raise RuntimeError("Uptime has not been restored")
        return (
            self._restored_us
            + (time.monotonic_ns() - self._monotonic_origin_ns) // 1000
        )

    async def save_state(self, *, final: bool = False) -> float:
        """Save a checkpoint; return confirmed accumulated duration in seconds."""
        async with self._lock:
            if self._checkpoint is None:
                raise RuntimeError("Uptime has not been restored")
            now_us = time.time_ns() // 1000
            accumulated_us = self.elapsed_microseconds()
            checkpoint = UptimeCheckpoint(
                self._checkpoint.period_id, now_us, accumulated_us
            )
            await self._repository.save(checkpoint, boot_id=self._boot_id, final=final)
            self._checkpoint = checkpoint
            return accumulated_us / 1_000_000
