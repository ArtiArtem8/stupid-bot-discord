"""Music controller delivery and recovery capability boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import discord

from stupidbot.features.music.playback.models import Track

if TYPE_CHECKING:
    import discord

    from stupidbot.features.music.playback.models import PlaybackAttempt
    from stupidbot.features.music.playback.player import MusicPlayer


class ControllerManagerProtocol(Protocol):
    """Protocol for controller management."""

    async def refresh_for_attempt(
        self, player: MusicPlayer, attempt: PlaybackAttempt
    ) -> None:
        """Refresh only the controller owned by the exact player and attempt."""
        ...

    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
        attempt: PlaybackAttempt,
    ) -> None:
        """Create a controller for a user."""
        ...

    async def destroy_for_guild(
        self,
        guild_id: int,
        reason: ControllerDestroyReason,
        *,
        expected_attempt_id: int | None = None,
        expected_player: MusicPlayer | None = None,
    ) -> None:
        """Destroy controller for a guild."""
        ...


class HealerProtocol(Protocol):
    async def capture_and_heal(self, guild_id: int) -> bool: ...


class ControllerDestroyReason(StrEnum):
    """Reasons that an active controller is no longer valid."""

    TRACK_END = "track_end"
    TRACK_CHANGED = "track_changed"
    TRACK_EXCEPTION = "track_exception"
    TRACK_STUCK = "track_stuck"
    VOICE_DISCONNECT = "voice_disconnect"
    PLAYER_ERROR = "player_error"
    MESSAGE_DELETED = "message_deleted"
    MANUAL_STOP = "manual_stop"
    SKIP = "skip"
    TIMEOUT = "timeout"
    STALE_VIEW = "stale_view"


@dataclass(frozen=True, slots=True)
class TrackExceptionPayload:
    """Payload for track exception events dispatched to the bot layer."""

    guild_id: int
    track: Track
    reason: str
    severity: str | None
    requester_id: int | None
    channel_id: int | None
