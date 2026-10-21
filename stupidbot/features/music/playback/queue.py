"""Ordered playback requests and repeat mode."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from stupidbot.features.music.playback.models import QueueEntry, RepeatMode


class QueueManager:
    """Keep waiting queue entries in playback order."""

    def __init__(self) -> None:
        self._queue: deque[QueueEntry] = deque()

    def __len__(self) -> int:
        """Count queued requests, excluding the current track."""
        return len(self._queue)

    def __iter__(self) -> Iterator[QueueEntry]:
        """Iterate over queued entries in playback order."""
        return iter(self._queue)

    @property
    def next(self) -> QueueEntry | None:
        """Peek at the next entry without removing it."""
        return self._queue[0] if self._queue else None

    @property
    def is_empty(self) -> bool:
        return not self._queue

    @property
    def duration(self) -> int:
        """Total duration of queued tracks in milliseconds."""
        return sum(entry.track.length for entry in self._queue)

    def append(self, entry: QueueEntry) -> None:
        self._queue.append(entry)

    def extend(self, entries: Iterable[QueueEntry]) -> None:
        self._queue.extend(entries)

    def prepend(self, entry: QueueEntry) -> None:
        self._queue.appendleft(entry)

    def extend_front(self, entries: Iterable[QueueEntry]) -> None:
        """Prepend entries in their original order."""
        self._queue.extendleft(reversed(tuple(entries)))

    def pop_next(self) -> QueueEntry | None:
        """Remove the next entry, or return None when the queue is empty."""
        if not self._queue:
            return None
        return self._queue.popleft()

    def remove_entries(
        self,
        expected: Sequence[QueueEntry],
    ) -> tuple[QueueEntry, ...]:
        """Remove waiting entries matching the stable queue-entry identity."""
        if not expected:
            return ()

        expected_ids = {entry.request_id for entry in expected}
        remaining: deque[QueueEntry] = deque()
        removed: list[QueueEntry] = []
        for queued_entry in self._queue:
            if queued_entry.request_id in expected_ids:
                removed.append(queued_entry)
            else:
                remaining.append(queued_entry)
        self._queue = remaining
        return tuple(removed)

    def shuffle(self) -> None:
        if len(self._queue) < 2:
            return
        temp = list(self._queue)
        random.shuffle(temp)
        self._queue = deque(temp)

    def clear(self) -> None:
        self._queue.clear()

    def snapshot(self) -> tuple[QueueEntry, ...]:
        """Return the queue contents in playback order."""
        return tuple(self._queue)

    def restore(self, entries: Iterable[QueueEntry]) -> None:
        """Replace the queue contents while preserving entry identity."""
        self._queue = deque(entries)


@dataclass(slots=True)
class RepeatManager:
    """Track the active repeat mode."""

    mode: RepeatMode = RepeatMode.OFF

    def toggle(self) -> RepeatMode:
        """Toggle between OFF and QUEUE."""
        self.mode = RepeatMode.QUEUE if self.mode is RepeatMode.OFF else RepeatMode.OFF
        return self.mode
