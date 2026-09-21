"""UTC calendar projections over credited human intervals."""

from datetime import UTC, date, timedelta

from api.voice.read_models import ActivityProfile
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import VoiceTimeline


def activity(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> ActivityProfile:
    """Split time at UTC hour/day boundaries into exactly 24 and 7 buckets."""
    hours = [0.0] * 24
    weekdays = [0.0] * 7
    days: dict[date, float] = {}
    for room in observed_rooms(timeline, scope):
        if user_id not in room.humans:
            continue
        cursor = room.started_at.astimezone(UTC)
        end = room.ended_at.astimezone(UTC)
        while cursor < end:
            boundary = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )
            stop = min(end, boundary)
            seconds = (stop - cursor).total_seconds()
            hours[cursor.hour] += seconds
            weekdays[cursor.weekday()] += seconds
            days[cursor.date()] = days.get(cursor.date(), 0.0) + seconds
            cursor = stop
    return ActivityProfile(tuple(hours), tuple(weekdays), tuple(sorted(days.items())))
