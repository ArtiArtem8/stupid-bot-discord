"""Queue and Repeat management."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .models import RepeatMode, Track


class QueueManager:
    """Manages the track queue using a deque."""

    def __init__(self) -> None:
        self._queue: deque[Track] = deque()

    def __len__(self) -> int:
        """Return the number of tracks in the queue."""
        return len(self._queue)

    def __iter__(self) -> Iterator[Track]:
        """Return an iterator over the tracks in the queue."""
        return iter(self._queue)

    @property
    def next(self) -> Track | None:
        """Peek at the next track without removing it."""
        return self._queue[0] if self._queue else None

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return not self._queue

    @property
    def duration(self) -> int:
        """Total duration of queue in milliseconds."""
        return sum(t.length for t in self._queue)

    def append(self, track: Track) -> None:
        """Add a single track to the end of the queue."""
        self._queue.append(track)

    def extend(self, tracks: Iterable[Track]) -> None:
        """Add multiple tracks to the end of the queue."""
        self._queue.extend(tracks)

    def prepend(self, track: Track) -> None:
        """Add a single track to the front of the queue."""
        self._queue.appendleft(track)

    def extend_front(self, tracks: Iterable[Track]) -> None:
        """Add multiple tracks to the front of the queue, preserving order."""
        self._queue.extendleft(reversed(tuple(tracks)))

    def pop_next(self) -> Track | None:
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
