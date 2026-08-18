import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

import config
from utils import AsyncJsonFileStore
from utils.json_types import JsonObject

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UptimeData:
    last_shutdown: float
    accumulated_uptime: float

    @staticmethod
    def _to_float(value: object, *, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return default

    @classmethod
    def from_json(cls, obj: Mapping[str, object] | None) -> Self | None:
        if not obj:
            return None
        return cls(
            last_shutdown=cls._to_float(obj.get("last_shutdown")),
            accumulated_uptime=cls._to_float(obj.get("accumulated_uptime")),
        )

    def to_json(self) -> JsonObject:
        return {
            "last_shutdown": self.last_shutdown,
            "accumulated_uptime": self.accumulated_uptime,
        }


class UptimeManager:
    def __init__(self, store: AsyncJsonFileStore | None = None):
        self.start_time: float = time.time()
        self.last_activity_str = "N/A"
        self._store = store or AsyncJsonFileStore(config.LAST_RUN_FILE, backup_amount=1)
        self._persistence_available = True

    async def restore_uptime(self) -> None:
        """Logic to resume accumulated uptime if restart was quick."""
        try:
            last_run = UptimeData.from_json(await self._store.read())
        except Exception:
            self._persistence_available = False
            logger.exception(
                "Failed to restore uptime; preserving the existing state file"
            )
            return
        if last_run is None:
            return

        disconnect_time = time.time() - last_run.last_shutdown

        if disconnect_time < config.DISCONNECT_TIMER_THRESHOLD:
            self.start_time = time.time() - last_run.accumulated_uptime
            logger.info("Resuming uptime (Offline for %.0fs)", disconnect_time)
        else:
            logger.info(
                "Offline time (%.0fs) exceeded threshold; Resetting uptime.",
                disconnect_time,
            )

    async def save_state(self) -> float:
        """Saves the current uptime state to file."""
        current_uptime = time.time() - self.start_time
        state = UptimeData(last_shutdown=time.time(), accumulated_uptime=current_uptime)
        if not self._persistence_available:
            return current_uptime
        try:
            await self._store.write(state.to_json())
        except Exception:
            logger.exception("Failed to save state")
        return current_uptime
