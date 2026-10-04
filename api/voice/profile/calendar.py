"""Daily wall-clock spans, independent of chart geometry and session grouping.

Elapsed durations stay in UTC. Repeated local times share a clock position;
clock-change spans explicitly mark that ambiguity instead of inventing a
24-hour elapsed day. Future time is separate from missing observations.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from api.voice.scope import TimeRange, VoiceScope, observation_ranges, observed_rooms
from api.voice.timeline import VoiceTimeline


@dataclass(frozen=True, slots=True, order=True)
class ClockSpan:
    """Half-open local minutes in [0, 1440], not elapsed duration on DST days."""

    start_minute: float
    end_minute: float


@dataclass(frozen=True, slots=True)
class DailyVoiceActivity:
    """Exact elapsed totals and wall-clock locations for one local date."""

    date: date
    voice_seconds: float
    observed_seconds: float
    possible_seconds: float
    voice_spans: tuple[ClockSpan, ...] = ()
    missing_spans: tuple[ClockSpan, ...] = ()
    future_spans: tuple[ClockSpan, ...] = ()
    clock_change_spans: tuple[ClockSpan, ...] = ()

    @property
    def coverage_ratio(self) -> float:
        """Return observed/requested elapsed time; zero for an empty window."""
        return (
            self.observed_seconds / self.possible_seconds
            if self.possible_seconds
            else 0
        )


def daily_voice_activity(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    window: TimeRange,
    timezone: tzinfo,
) -> tuple[DailyVoiceActivity, ...]:
    """Project all local dates through the window end, including a partial today."""
    rooms = observed_rooms(timeline, VoiceScope(guild_id, time_range=window))
    voice = tuple(
        TimeRange(room.started_at.astimezone(UTC), room.ended_at.astimezone(UTC))
        for room in rooms
        if user_id in room.humans
    )
    coverage = observation_ranges(timeline, guild_id)
    first, last = (
        moment.astimezone(timezone).date() for moment in (window.start, window.end)
    )
    return tuple(
        _day(first + timedelta(days=offset), window, voice, coverage, timezone)
        for offset in range((last - first).days + 1)
    )


def _day(
    day: date,
    window: TimeRange,
    voice: tuple[TimeRange, ...],
    coverage: tuple[TimeRange, ...],
    timezone: tzinfo,
) -> DailyVoiceActivity:
    start = datetime.combine(day, time.min, timezone).astimezone(UTC)
    midnight = datetime.combine(day + timedelta(days=1), time.min, timezone).astimezone(
        UTC
    )
    end = min(midnight, window.end)
    lower = max(start, window.start)
    parts = _clock_parts(start, midnight, timezone)
    present = _clip(voice, lower, end)
    observed = _clip(coverage, lower, end)
    return DailyVoiceActivity(
        day,
        sum((item.end - item.start).total_seconds() for item in present),
        sum((item.end - item.start).total_seconds() for item in observed),
        (end - lower).total_seconds(),
        _clock_spans(present, parts),
        _clock_spans(_unobserved(lower, end, observed), parts),
        _clock_spans((TimeRange(end, midnight),) if end < midnight else (), parts),
        _clock_changes(parts),
    )


def _clip(
    windows: tuple[TimeRange, ...], start: datetime, end: datetime
) -> tuple[TimeRange, ...]:
    if start >= end:
        return ()
    return tuple(
        TimeRange(max(start, item.start), min(end, item.end))
        for item in windows
        if item.start < end and start < item.end
    )


def _unobserved(
    start: datetime, end: datetime, coverage: tuple[TimeRange, ...]
) -> tuple[TimeRange, ...]:
    missing: list[TimeRange] = []
    cursor = start
    for item in coverage:
        if cursor < item.start:
            missing.append(TimeRange(cursor, item.start))
        cursor = item.end
    if cursor < end:
        missing.append(TimeRange(cursor, end))
    return tuple(missing)


def _clock_parts(
    start: datetime, end: datetime, timezone: tzinfo
) -> tuple[tuple[TimeRange, float], ...]:
    parts: list[tuple[TimeRange, float]] = []
    cursor = boundary = start
    while cursor < end:
        stop = min(cursor + timedelta(hours=1), end)
        if (
            cursor.astimezone(timezone).utcoffset()
            != stop.astimezone(timezone).utcoffset()
        ):
            stop = _offset_boundary(cursor, stop, timezone)
            parts.append((TimeRange(boundary, stop), _clock_minute(boundary, timezone)))
            boundary = stop
        cursor = stop
    if boundary < end:
        parts.append((TimeRange(boundary, end), _clock_minute(boundary, timezone)))
    return tuple(parts)


def _offset_boundary(lower: datetime, upper: datetime, timezone: tzinfo) -> datetime:
    offset = lower.astimezone(timezone).utcoffset()
    while upper - lower > timedelta(microseconds=1):
        middle = lower + (upper - lower) // 2
        if middle.astimezone(timezone).utcoffset() == offset:
            lower = middle
        else:
            upper = middle
    return upper


def _clock_minute(moment: datetime, timezone: tzinfo) -> float:
    local = moment.astimezone(timezone)
    return (
        local.hour * 60
        + local.minute
        + local.second / 60
        + local.microsecond / 60_000_000
    )


def _clock_spans(
    windows: tuple[TimeRange, ...], parts: tuple[tuple[TimeRange, float], ...]
) -> tuple[ClockSpan, ...]:
    spans: list[ClockSpan] = []
    for part, minute in parts:
        for item in _clip(windows, part.start, part.end):
            spans.append(
                ClockSpan(
                    minute + (item.start - part.start).total_seconds() / 60,
                    minute + (item.end - part.start).total_seconds() / 60,
                )
            )
    return _merge_spans(spans)


def _clock_changes(parts: tuple[tuple[TimeRange, float], ...]) -> tuple[ClockSpan, ...]:
    changes: list[ClockSpan] = []
    previous_end = 0.0
    for part, minute in parts:
        if minute != previous_end:
            changes.append(
                ClockSpan(min(minute, previous_end), max(minute, previous_end))
            )
        previous_end = minute + (part.end - part.start).total_seconds() / 60
    if previous_end != 1440:
        changes.append(ClockSpan(min(previous_end, 1440), max(previous_end, 1440)))
    return _merge_spans(changes)


def _merge_spans(spans: list[ClockSpan]) -> tuple[ClockSpan, ...]:
    merged: list[ClockSpan] = []
    for span in sorted(spans):
        if merged and span.start_minute <= merged[-1].end_minute:
            previous = merged[-1]
            merged[-1] = ClockSpan(
                previous.start_minute, max(previous.end_minute, span.end_minute)
            )
        else:
            merged.append(span)
    return tuple(merged)
