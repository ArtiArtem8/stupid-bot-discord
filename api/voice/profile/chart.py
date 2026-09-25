"""Calendar clipping and observation-aware 14-day activity projection."""

from datetime import UTC, datetime, time, timedelta, tzinfo

from api.voice.metrics.activity import activity
from api.voice.profile.model import DailyActivityPoint
from api.voice.scope import VoiceScope
from api.voice.timeline import VoiceTimeline


def chart_days(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    now: datetime,
    timezone: tzinfo,
) -> tuple[DailyActivityPoint, ...]:
    """Project 14 local days; a day is usable only with 90% positive coverage."""
    today = now.astimezone(timezone).date()
    daily_voice = dict(
        activity(
            timeline, user_id, VoiceScope(guild_id=guild_id), timezone=timezone
        ).daily_seconds
    )
    points: list[DailyActivityPoint] = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        start = datetime.combine(day, time.min, timezone).astimezone(UTC)
        next_day = datetime.combine(
            day + timedelta(days=1), time.min, timezone
        ).astimezone(UTC)
        end = min(next_day, now.astimezone(UTC))
        expected = max(0.0, (end - start).total_seconds())
        covered = sum(
            max(
                0.0,
                (
                    min(end, interval.ended_at) - max(start, interval.started_at)
                ).total_seconds(),
            )
            for interval in timeline.coverage
            if interval.guild_id == guild_id
            and interval.started_at < end
            and interval.ended_at > start
        )
        ratio = min(1.0, covered / expected) if expected else 0.0
        usable = ratio >= 0.90
        history = points[-2:]
        average = (
            (sum(p.voice_seconds for p in history) + daily_voice.get(day, 0.0)) / 3
            if usable and len(history) == 2 and all(p.usable for p in history)
            else None
        )
        points.append(
            DailyActivityPoint(
                day,
                daily_voice.get(day, 0.0),
                covered,
                expected,
                ratio,
                usable,
                average,
                day == today,
            )
        )
    return tuple(points)
