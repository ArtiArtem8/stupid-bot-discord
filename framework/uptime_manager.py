import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

import config
from utils import get_json, save_json
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
        if obj is None:
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
    def __init__(self):
        self.start_time: float = time.time()
        self.last_activity_str = "N/A"
        self._restore_uptime()

    def _restore_uptime(self):
        """Logic to resume accumulated uptime if restart was quick."""
        last_run = UptimeData.from_json(get_json(config.LAST_RUN_FILE))
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

    def save_state(self) -> float:
        """Saves the current uptime state to file."""
        current_uptime = time.time() - self.start_time
        state = UptimeData(last_shutdown=time.time(), accumulated_uptime=current_uptime)
        try:
            save_json(config.LAST_RUN_FILE, state.to_json(), backup_amount=1)
        except Exception as e:
            logger.error("Failed to save state: %s", e)
        return current_uptime
