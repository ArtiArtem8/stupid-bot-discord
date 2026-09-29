"""Project the guild-local lifetime values displayed by the voice card."""

from api.progression.appearance import LevelAppearancePolicy
from api.progression.levels import LevelPolicy
from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.profile.model import VoiceProfile
from api.voice.scope import VoiceScope
from api.voice.timeline import VoiceTimeline


def build_profile(
    timeline: VoiceTimeline,
    user_id: int,
    guild_id: int,
    timezone_label: str,
) -> VoiceProfile:
    """Calculate presence and XP; preserve exact XP through level selection."""
    scope = VoiceScope(guild_id=guild_id)
    observed = presence(timeline, user_id, scope)
    xp = VoiceXpPolicy().calculate(timeline, user_id, scope)
    progress = LevelPolicy().progress(xp)
    return VoiceProfile(
        guild_id=guild_id,
        user_id=user_id,
        level=progress.level,
        total_xp=int(xp),
        level_earned_xp=int(progress.earned),
        level_required_xp=progress.required,
        progress_ratio=float(progress.ratio),
        appearance=LevelAppearancePolicy().for_level(progress.level),
        total_voice_seconds=observed.total_seconds,
        session_count=observed.session_count,
        timezone_label=timezone_label,
    )
