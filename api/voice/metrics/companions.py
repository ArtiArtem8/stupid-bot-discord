"""Human co-presence projections; room overlap is counted once per pair."""

from api.voice.read_models import CompanionStat
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import VoiceTimeline


def companions(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> tuple[CompanionStat, ...]:
    """Return shared time per human, with exactly-two-occupant private time."""
    shared: dict[int, float] = {}
    private: dict[int, float] = {}
    for room in observed_rooms(timeline, scope):
        if user_id not in room.humans:
            continue
        for companion in room.humans:
            if companion == user_id:
                continue
            shared[companion] = shared.get(companion, 0.0) + room.seconds
            if len(room.states) == 2:
                private[companion] = private.get(companion, 0.0) + room.seconds
    return tuple(
        CompanionStat(user, seconds, private.get(user, 0.0))
        for user, seconds in sorted(shared.items())
    )
