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
    session_id: str | None


def presence(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> PresenceStat:
    """Measure a human's credited time; bot/unknown users never earn presence.

    Visits merge adjacent intervals across flag changes and channel moves in a
    guild, but split at a gap or changed Discord session ID. Query clipping can
    truncate visits. Solo requires one known human and no unidentified occupant;
    bots do not increase human group size.
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
    durations = _visit_durations(rooms, user_id)
    return PresenceStat(
        total,
        solo,
        group,
        len(durations),
        mean(durations) if durations else 0.0,
        median(durations) if durations else 0.0,
    )


def _visit_durations(rooms: tuple[RoomInterval, ...], user_id: int) -> list[float]:
    visits: list[_Visit] = []
    latest: dict[int, _Visit] = {}
    for room in rooms:
        state = next(state for state in room.states if state.user_id == user_id)
        previous = latest.get(room.guild_id)
        if (
            previous is not None
            and previous.ended_at == room.started_at
            and (
                previous.session_id is None
                or state.session_id is None
                or previous.session_id == state.session_id
            )
        ):
            previous.ended_at = room.ended_at
            if state.session_id is not None:
                previous.session_id = state.session_id
        else:
            visit = _Visit(room.started_at, room.ended_at, state.session_id)
            visits.append(visit)
            latest[room.guild_id] = visit
    return [(visit.ended_at - visit.started_at).total_seconds() for visit in visits]
