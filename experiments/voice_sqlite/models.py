"""Use production replay and projections at a fixed recorded observation time."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from api.voice.model import VoiceJournalRecord
from api.voice.profile.build import build_profile
from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.queries import user_summary
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import VoiceTimeline, build_timeline


def contexts(timeline: VoiceTimeline) -> tuple[tuple[int, int], ...]:
    """List observed guild/user pairs, including unknown identities and bots."""
    return tuple(
        sorted(
            {
                (room.guild_id, state.user_id)
                for room in timeline.rooms
                for state in room.states
            }
        )
    )


def project(
    timeline: VoiceTimeline, guild_id: int, user_id: int, as_of: datetime
) -> tuple[object, ...]:
    """Build all four card models with one fixed clock and the current policies."""
    timezone = ZoneInfo("Asia/Krasnoyarsk")
    return (
        build_profile(timeline, user_id, guild_id, str(timezone)),
        build_activity_detail(timeline, user_id, guild_id, as_of, timezone),
        build_people_detail(timeline, user_id, guild_id, as_of, timezone),
        build_xp_detail(timeline, user_id, guild_id, as_of, timezone),
    )


def verify_models(
    expected: tuple[VoiceJournalRecord, ...], actual: tuple[VoiceJournalRecord, ...]
) -> int:
    """Require exact facts, timeline, card models and scoped/global summaries."""
    if expected != actual:
        raise ValueError("Voice fact order or content changed")
    before, after = build_timeline(expected), build_timeline(actual)
    if before != after:
        raise ValueError("Voice timeline changed")
    start, end = (
        min(r.observed_at for r in expected),
        max(r.observed_at for r in expected),
    )
    checked = 0
    for gid, uid in contexts(before):
        if project(before, gid, uid, end) != project(after, gid, uid, end):
            raise ValueError("Voice card read model changed")
        for scope in (
            VoiceScope(guild_id=gid),
            VoiceScope(time_range=TimeRange(start, end)),
        ):
            if user_summary(before, uid, scope, timezone=UTC) != user_summary(
                after, uid, scope, timezone=UTC
            ):
                raise ValueError("Scoped or global voice summary changed")
        checked += 1
    return checked
