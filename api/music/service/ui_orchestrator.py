from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
import mafic
from discord.ext import commands

from api.music.protocols import ControllerManagerProtocol
from api.music.service.state_manager import StateManager

if TYPE_CHECKING:
    from api.music.player import MusicPlayer

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

    async def spawn_controller(self, player: MusicPlayer, track: mafic.Track) -> None:
        """Helper to safely spawn a UI controller."""
        requester_info = player.get_requester(track)
        if not requester_info:
            logger.debug("No requester found for track: %s", track.title)
            return

        # Determine best channel: Explicit > Most Used
        channel_id = requester_info.channel_id
        if not channel_id:
            session = self.state.get_session(player.guild.id)
            if session and session.channel_usage:
                channel_id = max(
                    session.channel_usage, key=lambda k: session.channel_usage[k]
                )

        if not channel_id:
            logger.debug("No channel found for track: %s", track.title)
            return

        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            logger.debug("No channel found for track: %s", track.title)
            return

        # Only show controller for tracks > 45s and < 2**63 - 1 ms
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
            track=track,
        )
