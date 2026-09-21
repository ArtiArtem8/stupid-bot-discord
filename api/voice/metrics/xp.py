"""Versioned display-only XP policy; never a persistence ledger."""

from dataclasses import dataclass
from math import isfinite
from typing import ClassVar

from api.voice.metrics.presence import presence
from api.voice.scope import GLOBAL_SCOPE, VoiceScope
from api.voice.timeline import VoiceTimeline


@dataclass(frozen=True, slots=True)
class VoiceXpPolicyV1:
    """Award one display XP per observed human minute by default."""

    points_per_minute: float = 1.0
    version: ClassVar[str] = "voice-v1"

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        if not isfinite(self.points_per_minute) or self.points_per_minute < 0:
            raise ValueError("XP rate must be finite and nonnegative")

    def calculate(
        self, timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
    ) -> float:
        """Compute XP from credited presence, without changing facts."""
        return (
            presence(timeline, user_id, scope).total_seconds
            / 60
            * self.points_per_minute
        )
