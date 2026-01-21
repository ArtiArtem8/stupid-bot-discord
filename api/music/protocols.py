"""Protocols for the music module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import discord

    from .models import Track
    from .player import MusicPlayer


class ControllerManagerProtocol(Protocol):
    """Protocol for controller management."""

    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
        track: Track,
    ) -> None:
        """Create a controller for a user."""
        ...

    async def destroy_for_guild(self, guild_id: int) -> None:
        """Destroy controller for a guild."""
        ...


class HealerProtocol(Protocol):
    async def capture_and_heal(self, guild_id: int) -> None: ...
    async def cleanup_after_disconnect(
        self, guild_id: int, is_healing: bool = False
    ) -> None: ...
