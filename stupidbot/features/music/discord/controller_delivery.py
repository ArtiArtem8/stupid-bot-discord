from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from stupidbot.features.music.contracts import ControllerManagerProtocol
from stupidbot.features.music.runtime.session import main_session_channel_id
from stupidbot.features.music.runtime.state import StateManager

if TYPE_CHECKING:
    from stupidbot.features.music.playback.models import PlaybackAttempt, TrackRequester
    from stupidbot.features.music.playback.player import MusicPlayer

logger = logging.getLogger(__name__)


class UIOrchestrator:
    """Orchestrates the creation of the Music UI Controller."""

    def __init__(
        self,
        bot: commands.Bot,
        controller_manager: ControllerManagerProtocol,
        state_manager: StateManager,
    ) -> None:
        self.bot = bot
        self.controller = controller_manager
        self.state = state_manager

    def _resolve_channel(
        self, player: MusicPlayer, attempt: PlaybackAttempt
    ) -> tuple[discord.abc.Messageable, TrackRequester] | None:
        """Resolve the best messageable channel for a track."""
        track = attempt.entry.track
        requester_info = attempt.entry.requester
        if not requester_info:
            logger.debug("No requester found for track: %s", track.title)
            return None

        channel_id = requester_info.channel_id
        if not channel_id:
            session = self.state.get_session(player.guild.id)
            if session:
                channel_id = main_session_channel_id(session)

        if not channel_id:
            logger.debug("No channel found for track: %s", track.title)
            return None

        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            logger.debug("No channel found for track: %s", track.title)
            return None

        return channel, requester_info

    async def spawn_controller(
        self, player: MusicPlayer, attempt: PlaybackAttempt
    ) -> None:
        track = attempt.entry.track
        resolved = self._resolve_channel(player, attempt)
        if not resolved:
            return

        channel, requester_info = resolved

        # Streams and short tracks do not offer useful seek controls.
        if track.length <= 45_000 or track.stream:
            logger.debug(
                "Track too short or a stream: %s, %s", track.title, track.length
            )
            return

        await self.controller.create_for_user(
            guild_id=player.guild.id,
            user_id=requester_info.user_id,
            channel=channel,
            player=player,
            attempt=attempt,
        )
