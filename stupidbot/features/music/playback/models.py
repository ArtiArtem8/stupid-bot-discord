"""Playback identity, queue entries and transition outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import uuid4

import mafic

PLAYBACK_USER_DATA_KEY = "stupid_attempt_token"


type Track = mafic.Track


type Playlist = mafic.Playlist


type SearchResult = list[Track] | Playlist | None


type QueuePlacement = Literal["end", "next"]


type PlayPlacement = Literal["now", "end", "next"]


class RepeatMode(StrEnum):
    """Repeat modes."""

    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


@dataclass(frozen=True, slots=True)
class TrackRequester:
    """Information about who requested a track and where."""

    user_id: int
    channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One request with a stable ID across playback and session recovery."""

    entry_id: int
    track: Track
    requester: TrackRequester | None
    request_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, slots=True)
class PlaybackAttempt:
    """One runtime playback start for a queue entry."""

    attempt_id: int
    entry: QueueEntry
    event_token: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, slots=True)
class EnqueueOutcome:
    """Entries created by one enqueue operation and its optional playback start."""

    entries: tuple[QueueEntry, ...]
    started_attempt: PlaybackAttempt | None

    @property
    def started_from_enqueue(self) -> bool:
        """Whether this operation started one of the entries it created."""
        attempt = self.started_attempt
        return attempt is not None and any(
            entry is attempt.entry for entry in self.entries
        )

    @property
    def has_waiting_entries(self) -> bool:
        """Whether this enqueue operation left at least one entry waiting."""
        started_count = int(self.started_from_enqueue)
        return len(self.entries) > started_count


@dataclass(frozen=True, slots=True)
class TrackEndOutcome:
    """Classification and transition result for a Mafic track-end event."""

    ended_attempt: PlaybackAttempt | None
    started_attempt: PlaybackAttempt | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Snapshot of the current queue state."""

    current: QueueEntry | None
    queue: tuple[QueueEntry, ...]
    repeat_mode: RepeatMode
