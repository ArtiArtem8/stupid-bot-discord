"""Timezone-aware calendar projections over credited human intervals."""

from datetime import UTC, date, datetime, timedelta, tzinfo

from api.voice.read_models import ActivityProfile
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import VoiceTimeline


def activity(
    timeline: VoiceTimeline,
    user_id: int,
    scope: VoiceScope = GLOBAL_SCOPE,
    *,
    timezone: tzinfo = UTC,
) -> ActivityProfile:
    """Aggregate real elapsed seconds into local hours, weekdays and dates.

    Arithmetic stays in UTC across DST changes. Repeated local hours share one
    bucket; skipped hours receive no seconds. The default calendar is UTC.
    """
    hours = [0.0] * 24
    weekdays = [0.0] * 7
    days: dict[date, float] = {}
    for room in observed_rooms(timeline, scope):
        if user_id not in room.humans:
            continue
        cursor = room.started_at.astimezone(UTC)
        end = room.ended_at.astimezone(UTC)
        while cursor < end:
            local = cursor.astimezone(timezone)
            stop = min(end, _next_local_hour(cursor, timezone))
            seconds = (stop - cursor).total_seconds()
            hours[local.hour] += seconds
            weekdays[local.weekday()] += seconds
            days[local.date()] = days.get(local.date(), 0.0) + seconds
            cursor = stop
    return ActivityProfile(tuple(hours), tuple(weekdays), tuple(sorted(days.items())))


def _next_local_hour(moment: datetime, timezone: tzinfo) -> datetime:
    local = moment.astimezone(timezone)
    boundary = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    # A boundary may be ambiguous or nonexistent at a DST transition. Resolve
    # both folds in UTC and keep the first future instant, never wall duration.
    candidates = (boundary.replace(fold=fold).astimezone(UTC) for fold in (0, 1))
    return min(candidate for candidate in candidates if candidate > moment)
