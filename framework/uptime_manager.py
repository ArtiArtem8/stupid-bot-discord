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
        self.start_time = time.time()
        self.last_activity_str = "N/A"
        self._repository = repository
        self._boot_id = uuid4().hex
        self._checkpoint: UptimeCheckpoint | None = None
        self._started_us = 0
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
            self._started_us = now_us - checkpoint.accumulated_us
            self.start_time = self._started_us / 1_000_000

    async def save_state(self, *, final: bool = False) -> float:
        """Save a checkpoint; return confirmed accumulated duration in seconds."""
        async with self._lock:
            if self._checkpoint is None:
                raise RuntimeError("Uptime has not been restored")
            now_us = time.time_ns() // 1000
            accumulated_us = max(0, now_us - self._started_us)
            checkpoint = UptimeCheckpoint(
                self._checkpoint.period_id, now_us, accumulated_us
            )
            await self._repository.save(checkpoint, boot_id=self._boot_id, final=final)
            self._checkpoint = checkpoint
            return accumulated_us / 1_000_000
