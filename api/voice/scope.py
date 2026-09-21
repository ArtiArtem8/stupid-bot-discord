"""Shared spatial and half-open time filtering for every voice metric."""

from dataclasses import dataclass, replace
from datetime import datetime

from api.voice.model import require_aware
from api.voice.timeline import RoomInterval, VoiceTimeline


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Aware half-open [start, end) query window."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        require_aware(self.start)
        require_aware(self.end)
        if self.end <= self.start:
            raise ValueError("A time range must have positive duration")


@dataclass(frozen=True, slots=True)
class VoiceScope:
    """Global by default, optionally restricted to a guild and its channel."""

    guild_id: int | None = None
    channel_id: int | None = None
    time_range: TimeRange | None = None

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        if self.channel_id is not None and self.guild_id is None:
            raise ValueError("Channel scope requires a guild")

    def includes(self, room: RoomInterval) -> bool:
        """Return whether the room belongs to this spatial scope."""
        return (self.guild_id is None or self.guild_id == room.guild_id) and (
            self.channel_id is None or self.channel_id == room.channel_id
        )


GLOBAL_SCOPE = VoiceScope()
"""Immutable default spatial/time scope."""


def observed_rooms(
    timeline: VoiceTimeline, scope: VoiceScope = GLOBAL_SCOPE
) -> tuple[RoomInterval, ...]:
    """Select and clip credited rooms without changing the shared timeline."""
    result: list[RoomInterval] = []
    for room in timeline.rooms:
        selected = room
        if room.gaps or not scope.includes(room):
            continue
        if scope.time_range is not None:
            start = max(room.started_at, scope.time_range.start)
            end = min(room.ended_at, scope.time_range.end)
            if start >= end:
                continue
            selected = replace(room, started_at=start, ended_at=end)
        result.append(selected)
    return tuple(result)
