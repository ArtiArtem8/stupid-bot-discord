"""Immutable UI-facing values, independent of journal and replay internals."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction


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
    """Time with one human; private allows bots but no third or unknown human."""

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
    """Local activity: 24 hours, Monday-first weekdays, and dated active seconds."""

    hourly_seconds: tuple[float, ...]
    weekday_seconds: tuple[float, ...]
    daily_seconds: tuple[tuple[date, float], ...]


@dataclass(frozen=True, slots=True)
class VoiceXpBreakdown:
    """Exact XP components; reductions are positive amounts to subtract.

    A policy rate returns this award for one hour. An integrated explanation
    returns the same components accumulated over the requested period. Neither
    value is persisted or rounded. Bonuses remain visible before the cap, with
    the excess shown explicitly as bonus_cap_reduction.
    """

    solo_base: Fraction = Fraction()
    social_base: Fraction = Fraction()
    large_group_bonus: Fraction = Fraction()
    audio_reduction: Fraction = Fraction()
    stream_bonus: Fraction = Fraction()
    video_bonus: Fraction = Fraction()
    bonus_cap_reduction: Fraction = Fraction()

    @property
    def total(self) -> Fraction:
        """Return the exact sum after audio and contribution-cap reductions."""
        return (
            self.solo_base
            + self.social_base
            + self.large_group_bonus
            - self.audio_reduction
            + self.stream_bonus
            + self.video_bonus
            - self.bonus_cap_reduction
        )


@dataclass(frozen=True, slots=True)
class UserVoiceSummary:
    """A user summary; presence remains guild-additive, XP uses temporal MAX."""

    user_id: int
    presence: PresenceStat
    companions: tuple[CompanionStat, ...]
    bots: BotStat
    activity: ActivityProfile
    xp_breakdown: VoiceXpBreakdown
    xp_policy_version: str

    @property
    def xp(self) -> Fraction:
        """Return XP without rounding or duplicating the breakdown's total."""
        return self.xp_breakdown.total
