"""Immutable, display-ready voice profile values."""

from dataclasses import dataclass
from datetime import date

from api.progression.appearance import LevelAppearance


@dataclass(frozen=True, slots=True)
class DailyActivityPoint:
    """One local calendar day, with unknown observation kept distinct from zero."""

    day: date
    voice_seconds: float
    coverage_seconds: float
    expected_window_seconds: float
    coverage_ratio: float
    usable: bool
    moving_average_seconds: float | None
    today: bool


@dataclass(frozen=True, slots=True)
class VoiceProfileStats:
    """Lifetime primary values and server-local secondary insights."""

    total_voice_seconds: float
    session_count: int
    average_session_seconds: float
    social_ratio: float
    top_companion_id: int | None
    top_companion_seconds: float
    peak_hour: int | None
    xp_last_7_days: int


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """Pure card data; exact progression policy has already been applied."""

    guild_id: int
    user_id: int
    level: int
    total_xp: int
    level_earned_xp: int
    level_required_xp: int
    xp_to_next_level: int
    progress_ratio: float
    appearance: LevelAppearance
    stats: VoiceProfileStats
    days: tuple[DailyActivityPoint, ...]
    timezone_label: str
