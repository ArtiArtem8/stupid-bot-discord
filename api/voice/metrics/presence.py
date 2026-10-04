"""Human presence totals and contiguous observed visits."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median

from api.voice.read_models import PresenceStat
from api.voice.scope import (
    GLOBAL_SCOPE,
    TimeRange,
    VoiceScope,
    observation_ranges,
    observed_rooms,
)
from api.voice.timeline import RoomInterval, VoiceTimeline


@dataclass(slots=True)
class _Visit:
    ended_at: datetime
    voice_seconds: float


def presence(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> PresenceStat:
    """Measure a human's credited time; bot/unknown users never earn presence.

    Visits merge adjacent intervals across flag changes, channel moves and
    transport session-ID changes in a guild. Unobserved or absent time splits
    visits, except observed absences shorter than five minutes. Absences never
    contribute to visit duration; query clipping can truncate visits.
    Solo requires one known human and
    no unidentified occupant; bots do not increase human group size.
    """
    rooms = tuple(
        room for room in observed_rooms(timeline, scope) if user_id in room.humans
    )
    total = sum(room.seconds for room in rooms)
    solo = sum(
        room.seconds
        for room in rooms
        if len(room.humans) == 1
        and all(state.is_bot is not None for state in room.states)
    )
    group = sum(room.seconds for room in rooms if len(room.humans) >= 2)
    durations = _visit_durations(rooms, timeline)
    return PresenceStat(
        total,
        solo,
        group,
        len(durations),
        mean(durations) if durations else 0.0,
        median(durations) if durations else 0.0,
    )


def _visit_durations(
    rooms: tuple[RoomInterval, ...], timeline: VoiceTimeline
) -> list[float]:
    visits: list[_Visit] = []
    latest: dict[int, _Visit] = {}
    coverage = {
        guild_id: observation_ranges(timeline, guild_id)
        for guild_id in {room.guild_id for room in rooms}
    }
    for room in rooms:
        previous = latest.get(room.guild_id)
        if previous is not None and _continues_visit(
            previous, room, coverage[room.guild_id]
        ):
            previous.ended_at = room.ended_at
            previous.voice_seconds += room.seconds
        else:
            visit = _Visit(room.ended_at, room.seconds)
            visits.append(visit)
            latest[room.guild_id] = visit
    return [visit.voice_seconds for visit in visits]


def _continues_visit(
    previous: _Visit, room: RoomInterval, coverage: tuple[TimeRange, ...]
) -> bool:
    pause = room.started_at - previous.ended_at
    if pause == timedelta():
        return True
    return timedelta() < pause < timedelta(minutes=5) and any(
        window.start <= previous.ended_at and room.started_at <= window.end
        for window in coverage
    )
