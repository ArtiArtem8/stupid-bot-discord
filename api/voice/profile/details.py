"""Focused guild-local lifetime and recent profile projections.

Builders consume an immutable timeline and perform no I/O. Coverage describes
observation of the guild, independently of whether the user occupied a room.
Elapsed arithmetic uses UTC; calendar boundaries use the caller's timezone.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from fractions import Fraction

from api.progression.levels import LevelPolicy
from api.voice.metrics.activity import activity
from api.voice.metrics.bots import bot_presence
from api.voice.metrics.companions import companions
from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.prediction import VoiceHoursEstimate, estimate_voice_hours
from api.voice.read_models import BotStat, CompanionStat, PresenceStat, VoiceXpBreakdown
from api.voice.scope import TimeRange, VoiceScope, local_calendar_range
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
    """Lifetime companions with a separate recent presence/people summary."""

    period: DetailPeriod
    presence: PresenceStat
    companions: tuple[CompanionStat, ...]
    bots: BotStat
    lifetime_period: DetailPeriod | None
    recent_presence: PresenceStat
    recent_unique_people: int

    @property
    def unique_people(self) -> int:
        return len(self.companions)

    @property
    def private_seconds(self) -> float:
        return sum(person.private_seconds for person in self.companions)


@dataclass(frozen=True, slots=True)
class XpDetail:
    """Exact lifetime explanation and a separate recent total."""

    period: DetailPeriod
    breakdown: VoiceXpBreakdown
    lifetime_period: DetailPeriod | None
    recent_xp: Fraction
    estimate: VoiceHoursEstimate | None


def _period(
    timeline: VoiceTimeline, guild_id: int, as_of: datetime, timezone: tzinfo
) -> DetailPeriod:
    window = local_calendar_range(as_of, timezone, 30)
    today = as_of.astimezone(timezone).date()
    return DetailPeriod(
        window,
        window.start.astimezone(timezone).date(),
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


def _lifetime_period(
    timeline: VoiceTimeline, guild_id: int, as_of: datetime, timezone: tzinfo
) -> DetailPeriod | None:
    # Lifetime coverage starts at the earliest retained evidence, never an
    # invented account creation date. A wholly absent history has no denominator.
    starts = [
        item.started_at.astimezone(UTC)
        for item in (*timeline.rooms, *timeline.coverage, *timeline.gaps)
        if item.guild_id in (None, guild_id) and item.started_at < as_of
    ]
    if not starts:
        return None
    window = TimeRange(min(starts), as_of.astimezone(UTC))
    return DetailPeriod(
        window,
        window.start.astimezone(timezone).date(),
        as_of.astimezone(timezone).date(),
        str(timezone),
        _observed_seconds(timeline, guild_id, window.start, window.end),
    )


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
    lifetime = _lifetime_period(timeline, guild_id, as_of, timezone)
    recent_scope = VoiceScope(guild_id=guild_id, time_range=period.time_range)
    scope = VoiceScope(guild_id=guild_id, time_range=(lifetime or period).time_range)
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
        lifetime,
        presence(timeline, user_id, recent_scope),
        len(companions(timeline, user_id, recent_scope)),
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
    lifetime = _lifetime_period(timeline, guild_id, as_of, timezone)
    scope = VoiceScope(guild_id=guild_id, time_range=period.time_range)
    lifetime_scope = VoiceScope(
        guild_id=guild_id, time_range=(lifetime or period).time_range
    )
    policy = VoiceXpPolicy()
    breakdown = policy.explain(timeline, user_id, lifetime_scope)
    recent_xp = policy.explain(timeline, user_id, scope).total
    return XpDetail(
        period,
        breakdown,
        lifetime,
        recent_xp,
        estimate_voice_hours(
            timeline,
            user_id,
            scope,
            timezone,
            remaining_xp=LevelPolicy().progress(breakdown.total).remaining,
            recent_xp=recent_xp,
            xp_policy=policy,
        ),
    )
