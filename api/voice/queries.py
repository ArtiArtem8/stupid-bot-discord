"""Compose UI read models from an already reconstructed timeline."""

from api.voice.metrics.activity import activity
from api.voice.metrics.bots import bot_presence
from api.voice.metrics.companions import companions
from api.voice.metrics.presence import presence
from api.voice.metrics.xp import VoiceXpPolicyV1
from api.voice.read_models import UserVoiceSummary
from api.voice.scope import GLOBAL_SCOPE, VoiceScope
from api.voice.timeline import VoiceTimeline


def user_summary(
    timeline: VoiceTimeline,
    user_id: int,
    scope: VoiceScope = GLOBAL_SCOPE,
    *,
    xp_policy: VoiceXpPolicyV1 | None = None,
) -> UserVoiceSummary:
    """Prepare data for a future UI; callers reuse one timeline across queries."""
    xp_policy = xp_policy if xp_policy is not None else VoiceXpPolicyV1()
    return UserVoiceSummary(
        user_id,
        presence(timeline, user_id, scope),
        companions(timeline, user_id, scope),
        bot_presence(timeline, user_id, scope),
        activity(timeline, user_id, scope),
        xp_policy.calculate(timeline, user_id, scope),
        xp_policy.version,
    )
