"""Compose server-local lifetime and recent voice projections for one user."""

from datetime import datetime, timedelta, tzinfo

from api.progression.appearance import LevelAppearancePolicy
from api.progression.levels import LevelPolicy
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.profile.chart import chart_days
from api.voice.profile.model import VoiceProfile, VoiceProfileStats
from api.voice.queries import user_summary
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import VoiceTimeline


def build_profile(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    now: datetime,
    timezone: tzinfo,
    timezone_label: str,
) -> VoiceProfile:
    """Build display numbers while preserving exact XP for level selection."""
    scope = VoiceScope(guild_id=guild_id)
    xp_policy = VoiceXpPolicy()
    summary = user_summary(
        timeline, user_id, scope, xp_policy=xp_policy, timezone=timezone
    )
    progress = LevelPolicy().progress(summary.xp)
    appearance = LevelAppearancePolicy().for_level(progress.level)
    companion = min(
        summary.companions,
        key=lambda item: (-item.shared_seconds, item.user_id),
        default=None,
    )
    peak = (
        min(range(24), key=lambda hour: (-summary.activity.hourly_seconds[hour], hour))
        if any(summary.activity.hourly_seconds)
        else None
    )
    last_week = VoiceScope(
        guild_id=guild_id,
        time_range=TimeRange(now - timedelta(days=7), now),
    )
    stats = VoiceProfileStats(
        total_voice_seconds=summary.presence.total_seconds,
        session_count=summary.presence.session_count,
        average_session_seconds=summary.presence.average_session_seconds,
        social_ratio=(
            summary.presence.group_seconds / summary.presence.total_seconds
            if summary.presence.total_seconds
            else 0.0
        ),
        top_companion_id=companion.user_id if companion else None,
        top_companion_seconds=companion.shared_seconds if companion else 0.0,
        peak_hour=peak,
        xp_last_7_days=int(xp_policy.calculate(timeline, user_id, last_week)),
    )
    return VoiceProfile(
        guild_id,
        user_id,
        progress.level,
        int(summary.xp),
        int(progress.earned),
        progress.required,
        int(progress.remaining),
        float(progress.ratio),
        appearance,
        stats,
        chart_days(timeline, user_id, guild_id, now, timezone),
        timezone_label,
    )
