"""Queue and Repeat management."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable, Iterator, Reversible
from dataclasses import dataclass
from typing import overload

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

    @overload
    def add(self, tracks: Track, *, at_front: bool = False) -> None: ...
    @overload
    def add(self, tracks: Iterable[Track], *, at_front: bool = False) -> None: ...
    def add(self, tracks: Iterable[Track] | Track, *, at_front: bool = False) -> None:
        """Add track(s) to the queue.

        Args:
            tracks: A single Track object or a sequence of Track objects.
            at_front: If True, adds the track(s) to the beginning of the queue.

        """
        if isinstance(tracks, Iterable):
            if at_front:
                rev_tracks = (
                    reversed(tracks)
                    if isinstance(tracks, Reversible)
                    else reversed(tuple(tracks))
                )
                self._queue.extendleft(rev_tracks)
            else:
                self._queue.extend(tracks)
            return
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
