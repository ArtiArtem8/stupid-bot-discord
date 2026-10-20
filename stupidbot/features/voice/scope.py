"""Shared spatial and half-open time filtering for every voice metric."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta, tzinfo
from itertools import chain

from stupidbot.features.voice.facts import require_aware
from stupidbot.features.voice.timeline import RoomInterval, VoiceTimeline


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Aware half-open [start, end) query window."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Require aware bounds with positive duration."""
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
        """Require a guild when selecting a channel."""
        if self.channel_id is not None and self.guild_id is None:
            raise ValueError("Channel scope requires a guild")

    def includes(self, room: RoomInterval) -> bool:
        """Return whether the room belongs to this spatial scope."""
        return (self.guild_id is None or self.guild_id == room.guild_id) and (
            self.channel_id is None or self.channel_id == room.channel_id
        )


GLOBAL_SCOPE = VoiceScope()
"""Immutable default spatial/time scope."""


def local_calendar_range(as_of: datetime, timezone: tzinfo, dates: int) -> TimeRange:
    """Include today and preceding local dates, ending at the aware request time."""
    require_aware(as_of)
    if dates < 1:
        raise ValueError("Calendar windows require a positive date count")
    first = as_of.astimezone(timezone).date() - timedelta(days=dates - 1)
    return TimeRange(
        datetime.combine(first, time.min, timezone).astimezone(UTC),
        as_of.astimezone(UTC),
    )


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


def history_start(timeline: VoiceTimeline, guild_id: int) -> datetime | None:
    """Return the earliest retained guild evidence in UTC, or None.

    Rooms, coverage and applicable gaps establish the history boundary,
    independently of any member's first session. Global gaps apply to every
    guild; the boundary does not imply uninterrupted observation.
    """
    return min(
        (
            item.started_at.astimezone(UTC)
            for item in chain(timeline.rooms, timeline.coverage, timeline.gaps)
            if item.guild_id in (None, guild_id)
        ),
        default=None,
    )


def observation_ranges(timeline: VoiceTimeline, guild_id: int) -> tuple[TimeRange, ...]:
    """Union guild coverage in UTC, including confirmed empty rooms."""
    merged: list[TimeRange] = []
    for start, end in sorted(
        (item.started_at.astimezone(UTC), item.ended_at.astimezone(UTC))
        for item in timeline.coverage
        if item.guild_id == guild_id
    ):
        if end <= start:
            continue
        if merged and start <= merged[-1].end:
            merged[-1] = TimeRange(merged[-1].start, max(end, merged[-1].end))
        else:
            merged.append(TimeRange(start, end))
    return tuple(merged)
