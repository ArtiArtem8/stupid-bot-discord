"""Immutable values displayed by the compact voice card."""

from dataclasses import dataclass

from api.progression.appearance import LevelAppearance


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """Display numbers rounded only after exact progression policy is applied."""

    guild_id: int
    user_id: int
    level: int
    total_xp: int
    level_earned_xp: int
    level_required_xp: int
    progress_ratio: float
    appearance: LevelAppearance
    total_voice_seconds: float
    session_count: int
    timezone_label: str
