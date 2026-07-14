"""Queue and Repeat management."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .models import QueueEntry, RepeatMode


class QueueManager:
    """Manages the track queue using a deque."""

    def __init__(self) -> None:
        self._queue: deque[QueueEntry] = deque()

    def __len__(self) -> int:
        """Return the number of tracks in the queue."""
        return len(self._queue)

    def __iter__(self) -> Iterator[QueueEntry]:
        """Return an iterator over the tracks in the queue."""
        return iter(self._queue)

    @property
    def next(self) -> QueueEntry | None:
        """Peek at the next track without removing it."""
        return self._queue[0] if self._queue else None

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return not self._queue

    @property
    def duration(self) -> int:
        """Total duration of queue in milliseconds."""
        return sum(entry.track.length for entry in self._queue)

    def append(self, entry: QueueEntry) -> None:
        """Add a single track to the end of the queue."""
        self._queue.append(entry)

    def extend(self, entries: Iterable[QueueEntry]) -> None:
        """Add multiple tracks to the end of the queue."""
        self._queue.extend(entries)

    def prepend(self, entry: QueueEntry) -> None:
        """Add a single track to the front of the queue."""
        self._queue.appendleft(entry)

    def extend_front(self, entries: Iterable[QueueEntry]) -> None:
        """Add multiple tracks to the front of the queue, preserving order."""
        self._queue.extendleft(reversed(tuple(entries)))

    def pop_next(self) -> QueueEntry | None:
        """Pop the next track from the queue."""
        if not self._queue:
            return None
        return self._queue.popleft()

    def shuffle(self) -> None:
        """Shuffle the queue."""
        if len(self._queue) < 2:
            return
        temp = list(self._queue)
        random.shuffle(temp)
        self._queue = deque(temp)

    def clear(self) -> None:
        """Clear the queue."""
        self._queue.clear()

    def snapshot(self) -> tuple[QueueEntry, ...]:
        """Return the queue contents in playback order."""
        return tuple(self._queue)

    def restore(self, entries: Iterable[QueueEntry]) -> None:
        """Replace the queue contents while preserving entry identity."""
        self._queue = deque(entries)


@dataclass(slots=True)
class RepeatManager:
    """Manages repeat mode.

    Attributes:
        mode: The current repeat mode.

    """

    mode: RepeatMode = RepeatMode.OFF

    def toggle(self) -> RepeatMode:
        """Toggle between OFF and QUEUE."""
        self.mode = RepeatMode.QUEUE if self.mode is RepeatMode.OFF else RepeatMode.OFF
        return self.mode
