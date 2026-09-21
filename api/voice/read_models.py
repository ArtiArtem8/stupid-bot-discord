"""Immutable UI-facing values, independent of journal and replay internals."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PresenceStat:
    """Credited voice seconds and contiguous observed visits within a query."""

    total_seconds: float = 0.0
    solo_seconds: float = 0.0
    group_seconds: float = 0.0
    session_count: int = 0
    average_session_seconds: float = 0.0
    median_session_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class CompanionStat:
    """Time with one identified human; private means exactly two room occupants."""

    user_id: int
    shared_seconds: float
    private_seconds: float


@dataclass(frozen=True, slots=True)
class BotStat:
    """Co-presence with bots, without implying playback or other bot activity."""

    any_bot_seconds: float
    by_bot: tuple[tuple[int, float], ...]


@dataclass(frozen=True, slots=True)
class ActivityProfile:
    """UTC activity: 24 hours, Monday-first weekdays, and dated active seconds."""

    hourly_seconds: tuple[float, ...]
    weekday_seconds: tuple[float, ...]
    daily_seconds: tuple[tuple[date, float], ...]


@dataclass(frozen=True, slots=True)
class UserVoiceSummary:
    """A user summary ready for presentation without Discord or storage objects."""

    user_id: int
    presence: PresenceStat
    companions: tuple[CompanionStat, ...]
    bots: BotStat
    activity: ActivityProfile
    xp: float
    xp_policy_version: str
