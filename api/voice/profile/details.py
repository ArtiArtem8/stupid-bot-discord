"""Focused, guild-local projections for the last 30 local calendar dates.

Builders consume an immutable timeline and perform no I/O. Coverage describes
observation of the guild, independently of whether the user occupied a room.
Elapsed arithmetic uses UTC; calendar boundaries use the caller's timezone.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from api.voice.metrics.activity import activity
from api.voice.metrics.bots import bot_presence
from api.voice.metrics.companions import companions
from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.model import require_aware
from api.voice.read_models import BotStat, CompanionStat, PresenceStat, VoiceXpBreakdown
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import VoiceTimeline


@dataclass(frozen=True, slots=True)
class DailyVoiceActivity:
    """A local date; zero voice is only known where observation exists."""

    date: date
    voice_seconds: float
    observed_seconds: float
    possible_seconds: float

    @property
    def coverage_ratio(self) -> float:
        """Return observed/requested elapsed time, or zero for an empty window."""
        return (
            self.observed_seconds / self.possible_seconds
            if self.possible_seconds
            else 0
        )


@dataclass(frozen=True, slots=True)
class DetailPeriod:
    """One shared calendar window and its independent guild coverage."""

    time_range: TimeRange
    first_date: date
    last_date: date
    timezone_label: str
    observed_seconds: float

    @property
    def coverage_ratio(self) -> float:
        return (
            self.observed_seconds
            / (self.time_range.end - self.time_range.start).total_seconds()
        )


@dataclass(frozen=True, slots=True)
class ActivityDetail:
    """Presence and daily activity, with absent peaks represented explicitly."""

    period: DetailPeriod
    presence: PresenceStat
    days: tuple[DailyVoiceActivity, ...]
    peak_hour: int | None
    peak_weekday: int | None


@dataclass(frozen=True, slots=True)
class PeopleDetail:
    """Confirmed human overlaps, ordered by shared/private time then user ID."""

    period: DetailPeriod
    presence: PresenceStat
    companions: tuple[CompanionStat, ...]
    bots: BotStat

    @property
    def unique_people(self) -> int:
        return len(self.companions)

    @property
    def private_seconds(self) -> float:
        return sum(person.private_seconds for person in self.companions)


@dataclass(frozen=True, slots=True)
class XpDetail:
    """Exact policy explanation; reductions remain positive subtraction terms."""

    period: DetailPeriod
    breakdown: VoiceXpBreakdown


def _period(
    timeline: VoiceTimeline, guild_id: int, as_of: datetime, timezone: tzinfo
) -> DetailPeriod:
    require_aware(as_of)
    today = as_of.astimezone(timezone).date()
    first = today - timedelta(days=29)
    window = TimeRange(
        datetime.combine(first, time.min, timezone).astimezone(UTC),
        as_of.astimezone(UTC),
    )
    return DetailPeriod(
        window,
        first,
        today,
        str(timezone),
        _observed_seconds(timeline, guild_id, window.start, window.end),
    )


def _observed_seconds(
    timeline: VoiceTimeline, guild_id: int, start: datetime, end: datetime
) -> float:
    # Merge clipped intervals so overlapping replay coverage cannot exceed 100%.
    intervals = sorted(
        (
            max(start, item.started_at.astimezone(UTC)),
            min(end, item.ended_at.astimezone(UTC)),
        )
        for item in timeline.coverage
        if item.guild_id == guild_id
    )
    cursor = start
    seconds = 0.0
    for lower, upper in intervals:
        uncovered_start = max(lower, cursor)
        if upper > uncovered_start:
            seconds += (upper - uncovered_start).total_seconds()
            cursor = upper
    return seconds


def build_activity_detail(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    as_of: datetime,
    timezone: tzinfo = UTC,
) -> ActivityDetail:
    """Build 30 date columns, clipping today's possible time at as_of."""
    period = _period(timeline, guild_id, as_of, timezone)
    scope = VoiceScope(guild_id=guild_id, time_range=period.time_range)
    measured = activity(timeline, user_id, scope, timezone=timezone)
    daily = dict(measured.daily_seconds)
    days: list[DailyVoiceActivity] = []
    for offset in range(30):
        day = period.first_date + timedelta(days=offset)
        start = datetime.combine(day, time.min, timezone).astimezone(UTC)
        end = min(
            datetime.combine(day + timedelta(days=1), time.min, timezone).astimezone(
                UTC
            ),
            period.time_range.end,
        )
        days.append(
            DailyVoiceActivity(
                day,
                daily.get(day, 0.0),
                _observed_seconds(timeline, guild_id, start, end),
                (end - start).total_seconds(),
            )
        )
    return ActivityDetail(
        period,
        presence(timeline, user_id, scope),
        tuple(days),
        _peak(measured.hourly_seconds),
        _peak(measured.weekday_seconds),
    )


def _peak(values: tuple[float, ...]) -> int | None:
    return max(range(len(values)), key=values.__getitem__) if any(values) else None


def build_people_detail(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    as_of: datetime,
    timezone: tzinfo = UTC,
) -> PeopleDetail:
    """Build human and bot co-presence without computing activity or XP."""
    period = _period(timeline, guild_id, as_of, timezone)
    scope = VoiceScope(guild_id=guild_id, time_range=period.time_range)
    people = sorted(
        companions(timeline, user_id, scope),
        key=lambda person: (
            -person.shared_seconds,
            -person.private_seconds,
            person.user_id,
        ),
    )
    return PeopleDetail(
        period,
        presence(timeline, user_id, scope),
        tuple(people),
        bot_presence(timeline, user_id, scope),
    )


def build_xp_detail(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    as_of: datetime,
    timezone: tzinfo = UTC,
) -> XpDetail:
    """Explain only XP, preserving Fraction components through presentation."""
    period = _period(timeline, guild_id, as_of, timezone)
    scope = VoiceScope(guild_id=guild_id, time_range=period.time_range)
    return XpDetail(period, VoiceXpPolicy().explain(timeline, user_id, scope))
