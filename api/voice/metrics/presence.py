"""Human presence totals and contiguous observed visits."""

from dataclasses import dataclass
from datetime import datetime
from statistics import mean, median

from api.voice.read_models import PresenceStat
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import RoomInterval, VoiceTimeline


@dataclass(slots=True)
class _Visit:
    started_at: datetime
    ended_at: datetime


def presence(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> PresenceStat:
    """Measure a human's credited time; bot/unknown users never earn presence.

    Visits merge adjacent intervals across flag changes, channel moves and
    transport session-ID changes in a guild. Unobserved or absent time splits
    visits; query clipping can truncate them. Solo requires one known human and
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
    durations = _visit_durations(rooms)
    return PresenceStat(
        total,
        solo,
        group,
        len(durations),
        mean(durations) if durations else 0.0,
        median(durations) if durations else 0.0,
    )


def _visit_durations(rooms: tuple[RoomInterval, ...]) -> list[float]:
    visits: list[_Visit] = []
    latest: dict[int, _Visit] = {}
    for room in rooms:
        previous = latest.get(room.guild_id)
        if previous is not None and previous.ended_at == room.started_at:
            previous.ended_at = room.ended_at
        else:
            visit = _Visit(room.started_at, room.ended_at)
            visits.append(visit)
            latest[room.guild_id] = visit
    return [(visit.ended_at - visit.started_at).total_seconds() for visit in visits]
