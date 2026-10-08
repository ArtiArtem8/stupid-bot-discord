"""Protocols for the music module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import discord

    from .models import ControllerDestroyReason, PlaybackAttempt
    from .player import MusicPlayer


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
