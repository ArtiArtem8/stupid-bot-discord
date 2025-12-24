import logging
import time
from typing import TypedDict

import config
from utils import get_json, save_json

logger = logging.getLogger(__name__)


class UptimeData(TypedDict):
    last_shutdown: float
    accumulated_uptime: float


class UptimeManager:
    def __init__(self):
        self.start_time: float = time.time()
        self.last_activity_str = "N/A"
        self._restore_uptime()

    def _restore_uptime(self):
        """Logic to resume accumulated uptime if restart was quick."""
        last_run = get_json(config.LAST_RUN_FILE)
        if last_run is None:
            return

        last_shutdown = last_run.get("last_shutdown", 0.0)
        accumulated = last_run.get("accumulated_uptime", 0.0)
        disconnect_time = time.time() - last_shutdown

        if disconnect_time < config.DISCONNECT_TIMER_THRESHOLD:
            self.start_time = time.time() - accumulated
            logger.info("Resuming uptime (Offline for %.0fs)", disconnect_time)
        else:
            logger.info(
                "Offline time (%.0fs) exceeded threshold; Resetting uptime.",
                disconnect_time,
            )

    def save_state(self) -> float:
        """Saves the current uptime state to file."""
        current_uptime = time.time() - self.start_time
        data: UptimeData = {
            "last_shutdown": time.time(),
            "accumulated_uptime": current_uptime,
        }
        try:
            save_json(config.LAST_RUN_FILE, data, backup_amount=1)
        except Exception as e:
            logger.error("Failed to save state: %s", e)
        return current_uptime
