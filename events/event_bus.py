import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

from .base_event import BaseEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Coroutine[Any, Any, None]]


class EventBus:
    """Asynchronous Event Bus with telemetry support."""

    def __init__(self) -> None:
        self._subscribers: dict[type[BaseEvent], list[EventHandler]] = defaultdict(list)
        self._metrics: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)

    def subscribe(self, event_type: type[BaseEvent], handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__name__} to {event_type.__name__}")

    async def publish(self, event: BaseEvent) -> None:
        """Publish an event to all subscribers.

        Handlers are executed concurrently using asyncio.TaskGroup.
        """
        event_type = type(event)
        handlers = self._subscribers.get(event_type, [])

        if not handlers:
            logger.debug(f"No handlers for {event.event_name}")
            return

        self._metrics[f"event_published.{event.event_name}"] += 1

        start_time = time.perf_counter()

        try:
            async with asyncio.TaskGroup() as tg:
                for handler in handlers:
                    tg.create_task(self._process_handler(handler, event))
        except Exception as e:
            logger.error(
                f"Error publishing event {event.event_name}: {e}", exc_info=True
            )
            self._metrics[f"event_error.{event.event_name}"] += 1
        finally:
            duration = (time.perf_counter() - start_time) * 1000
            self._latencies[event.event_name].append(duration)
            # Keep latency list size manageable
            if len(self._latencies[event.event_name]) > 100:
                self._latencies[event.event_name].pop(0)

    async def _process_handler(self, handler: EventHandler, event: BaseEvent) -> None:
        try:
            await handler(event)
            self._metrics[f"handler_success.{handler.__name__}"] += 1
        except Exception as e:
            logger.error(
                f"Error in handler {handler.__name__} for {event.event_name}: {e}",
                exc_info=True,
            )
            self._metrics[f"handler_error.{handler.__name__}"] += 1

    def get_metrics(self) -> dict[str, Any]:
        """Return a snapshot of current metrics."""
        avg_latencies = {k: sum(v) / len(v) for k, v in self._latencies.items() if v}
        return {"counts": dict(self._metrics), "avg_latency_ms": avg_latencies}
