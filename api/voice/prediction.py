"""Conditional voice-hour estimates, separate from exact credited progression."""

from dataclasses import dataclass, replace
from datetime import UTC, date, timedelta, tzinfo
from fractions import Fraction
from typing import ClassVar

from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.scope import VoiceScope, local_calendar_range, observed_rooms
from api.voice.timeline import VoiceTimeline


@dataclass(frozen=True, slots=True)
class VoiceHoursEstimate:
    """A point estimate at the recent pooled pace, without a calibrated interval.

    Evidence describes confirmed voice only. Guild coverage remains a separate
    completeness measure; it is neither a probability nor an XP multiplier.
    """

    expected_hours: Fraction
    pace_xp_per_hour: Fraction
    evidence_hours: Fraction
    active_days: int
    model_version: ClassVar[str] = "voice-pace-mean30-v1"


def estimate_voice_hours(
    timeline: VoiceTimeline,
    user_id: int,
    scope: VoiceScope,
    timezone: tzinfo,
    *,
    remaining_xp: Fraction,
    recent_xp: Fraction,
    xp_policy: VoiceXpPolicy,
) -> VoiceHoursEstimate | None:
    """Estimate hours at pooled XP/voice time within the supplied 30-date scope.

    The caller supplies exact remaining and recent XP from canonical policies.
    At least one observed voice hour is required. The latest seven local dates
    must contain earning voice; neither inactivity nor zero XP falls back to an
    older pace. These are availability guards, not guarantees of forecast error.
    The function performs no I/O and never reweights awards by guild coverage.
    """
    window = scope.time_range
    if window is None or scope.guild_id is None:
        raise ValueError("Voice-hour estimates require a guild and finite window")
    evidence = Fraction(str(presence(timeline, user_id, scope).total_seconds)) / 3600
    if evidence < 1 or recent_xp <= 0:
        return None
    week = replace(scope, time_range=local_calendar_range(window.end, timezone, 7))
    if xp_policy.calculate(timeline, user_id, week) <= 0:
        return None
    pace = recent_xp / evidence
    return VoiceHoursEstimate(
        remaining_xp / pace,
        pace,
        evidence,
        len(_active_dates(timeline, user_id, scope, timezone)),
    )


def _active_dates(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope, timezone: tzinfo
) -> set[date]:
    days: set[date] = set()
    for room in observed_rooms(timeline, scope):
        if user_id not in room.humans:
            continue
        first = room.started_at.astimezone(timezone).date()
        # Half-open midnight endings do not create an extra active date.
        last = (
            (room.ended_at.astimezone(UTC) - timedelta(microseconds=1))
            .astimezone(timezone)
            .date()
        )
        days.update(
            first + timedelta(days=offset) for offset in range((last - first).days + 1)
        )
    return days
