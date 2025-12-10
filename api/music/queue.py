"""Queue and Repeat management."""

from __future__ import annotations

import random
from collections import deque

from .models import RepeatMode, Track


class QueueManager:
    """Manages the queue of tracks."""

    def __init__(self) -> None:
        self._queue: deque[Track] = deque()

    def __len__(self) -> int:
        """Return the number of tracks in the queue."""
        return len(self._queue)

    @property
    def tracks(self) -> deque[Track]:
        """Return the internal queue."""
        return self._queue

    @property
    def next(self) -> Track | None:
        """Peek at the next track."""
        if not self._queue:
            return None
        return self._queue[0]

    def add(self, tracks: list[Track] | Track, at_front: bool = False) -> None:
        """Add track(s) to the queue."""
        if isinstance(tracks, list):
            if at_front:
                self._queue.extendleft(reversed(tracks))
            else:
                self._queue.extend(tracks)
        else:
            if at_front:
                self._queue.appendleft(tracks)
            else:
                self._queue.append(tracks)

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

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return len(self._queue) == 0

    @property
    def duration_ms(self) -> int:
        """Total duration of tracks in queue."""
        return sum(t.length for t in self._queue)


class RepeatManager:
    """Manages repeat mode."""

    def __init__(self) -> None:
        self._mode: RepeatMode = RepeatMode.OFF

    @property
    def mode(self) -> RepeatMode:
        """Get current repeat mode."""
        return self._mode

    @mode.setter
    def mode(self, value: RepeatMode) -> None:
        """Set repeat mode."""
        self._mode = value

    def toggle(self) -> RepeatMode:
        """Toggle between OFF and QUEUE."""
        self._mode = (
            RepeatMode.QUEUE if self._mode is RepeatMode.OFF else RepeatMode.OFF
        )
        return self._mode
