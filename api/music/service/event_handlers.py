from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

import discord
import mafic
from discord.ext import commands

from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator

if TYPE_CHECKING:
    from api.music.player import MusicPlayer

logger = logging.getLogger(__name__)


class HealerProtocol(Protocol):
    async def capture_and_heal(self, guild_id: int) -> None: ...
    async def cleanup_after_disconnect(
        self, guild_id: int, is_healing: bool = False
    ) -> None: ...


class MusicEventHandlers:
    """Handles Discord and Mafic events for the music service."""

    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
        ui_orchestrator: UIOrchestrator,
        healer: HealerProtocol,
    ) -> None:
        self.bot = bot
        self.connection = connection_manager
        self.state = state_manager
        self.ui = ui_orchestrator
        self.healer = healer
        self._healing_guilds: set[int] = set()
        self._setup_done = False

    def setup(self) -> None:
        """Register event listeners."""
        if self._setup_done:
            logger.warning("MusicEventHandlers setup called multiple times.")
            return

        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self.on_node_ready, "on_node_ready")
        self.bot.add_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.add_listener(self._on_websocket_closed, "on_websocket_closed")
        self._setup_done = True

    def cleanup(self) -> None:
        """Remove event listeners."""
        if not self._setup_done:
            return

        self.bot.remove_listener(self._on_track_start, "on_track_start")
        self.bot.remove_listener(self._on_track_end, "on_track_end")
        self.bot.remove_listener(self.on_node_ready, "on_node_ready")
        self.bot.remove_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.remove_listener(self._on_websocket_closed, "on_websocket_closed")
        self._setup_done = False
        logger.info("MusicEventHandlers listeners removed.")

    async def on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
        logger.info("Lavalink node '%s' is ready", node.label)

    async def _on_track_start(self, event: mafic.TrackStartEvent[MusicPlayer]) -> None:
        if event.player.guild.id in self._healing_guilds:
            logger.debug(
                "Ignoring track_start during healing for guild %s",
                event.player.guild.id,
            )
            return

        player = event.player
        guild_id = player.guild.id
        track = event.track

        self.state.record_track_start(guild_id)
        logger.debug("Track started in guild %d: %s", guild_id, track.title)

        await self.ui.spawn_controller(player, track)

    async def _on_track_end(self, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
        if event.player.guild.id in self._healing_guilds:
            logger.debug(
                "Ignoring track_end during healing for guild %s",
                event.player.guild.id,
            )
            return

        player = event.player
        track = event.track
        reason = event.reason

        logger.debug("Track ended: %s (Reason: %s)", track.title, reason)

        self.state.record_history(player, track, reason)

        if (
            event.reason in (mafic.EndReason.FINISHED, mafic.EndReason.STOPPED)
            and player.queue.is_empty
            and not player.current
        ):
            logger.debug(
                "Playback ended (Reason: %s) with empty queue. "
                + "Destroying controller immediately.",
                event.reason,
            )
            await self.ui.controller.destroy_for_guild(player.guild.id)

        if reason in (mafic.EndReason.FINISHED, mafic.EndReason.LOAD_FAILED):
            await player.advance(previous_track=track)

    async def _on_websocket_closed(
        self, event: mafic.WebSocketClosedEvent[MusicPlayer]
    ) -> None:
        guild_id = event.player.guild.id
        logger.warning(
            "Voice websocket closed for guild %s. Code: %s, Reason: %s",
            event.player.guild.id,
            event.code,
            event.reason,
        )

        if event.code == 4006:
            logger.warning(
                "Detected 4006 for guild %s. Initiating HEALING protocol.",
                event.player.guild.id,
            )
            await self.heal(guild_id)

        elif event.code == 4014 and event.by_discord:
            await self.healer.cleanup_after_disconnect(event.player.guild.id)

    async def heal(self, guild_id: int) -> None:
        if guild_id in self._healing_guilds:
            return
        self._healing_guilds.add(guild_id)
        try:
            await self.ui.controller.destroy_for_guild(guild_id)
            self.state.cancel_timer(guild_id)
            await self.healer.capture_and_heal(guild_id)
        finally:
            self._healing_guilds.discard(guild_id)

    async def _on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not self.bot.user:
            return

        if member.id == self.bot.user.id:
            guild_id = member.guild.id
            if after.channel is None:
                logger.info(
                    "Bot was disconnected from guild %s. Cleaning up.", guild_id
                )
                await self.healer.cleanup_after_disconnect(
                    guild_id, is_healing=guild_id in self._healing_guilds
                )
                return

            if before.channel is not None and before.channel != after.channel:
                logger.info(
                    "Bot moved from %s to %s in guild %s. Continuing playback.",
                    before.channel.name,
                    after.channel.name,
                    guild_id,
                )
                if self.state.is_timer_active(guild_id):
                    await self._update_channel_timer(guild_id, after.channel)
                return

        guild = member.guild
        voice_client = guild.voice_client

        if not voice_client or not isinstance(
            voice_client, mafic.Player
        ):  # MusicPlayer
            return

        bot_channel = voice_client.channel
        if not bot_channel:
            return

        is_relevant = before.channel == bot_channel or after.channel == bot_channel

        if before.channel == bot_channel == after.channel:
            if before.deaf != after.deaf or before.self_deaf != after.self_deaf:
                is_relevant = True

        if is_relevant:
            if isinstance(bot_channel, (discord.VoiceChannel, discord.StageChannel)):
                await self._update_channel_timer(guild.id, bot_channel)

    async def _update_channel_timer(
        self, guild_id: int, channel: discord.VoiceChannel | discord.StageChannel
    ) -> None:
        human_members = [m for m in channel.members if not m.bot]
        effectively_empty = False
        empty_reason: str | None = None

        if len(human_members) == 0:
            effectively_empty = True
            empty_reason = "empty"
        else:
            all_deafened = all(
                (m.voice.self_deaf or m.voice.deaf)
                for m in human_members
                if m.voice is not None
            )
            if all_deafened:
                effectively_empty = True
                empty_reason = "all_deafened"

        if effectively_empty:
            if not self.state.is_timer_active(guild_id):
                logger.info(
                    "Channel %s in guild %s is effectively empty (%s). Starting timer.",
                    channel.name,
                    guild_id,
                    empty_reason,
                )
                self.state.start_timer(guild_id, empty_reason)
        else:
            if self.state.is_timer_active(guild_id):
                logger.info(
                    "Channel %s in guild %s is no longer empty. Cancelling timer.",
                    channel.name,
                    guild_id,
                )
                self.state.cancel_timer(guild_id)
