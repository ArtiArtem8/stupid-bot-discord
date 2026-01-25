from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
import mafic
from discord.ext import commands
from mafic.typings import LavalinkException

from api.music.models import TrackExceptionPayload, TrackId
from api.music.protocols import HealerProtocol
from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator

if TYPE_CHECKING:
    from api.music.player import MusicPlayer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoadFailureState:
    """Tracks notification and cleanup state for a failed track load."""

    track_id: str
    reason: str
    severity: str | None
    notified: bool = False
    handled: bool = False


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
        self._load_failures: dict[int, LoadFailureState] = {}
        self._setup_done = False

    def setup(self) -> None:
        """Register event listeners."""
        if self._setup_done:
            logger.warning("MusicEventHandlers setup called multiple times.")
            return

        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_track_exception, "on_track_exception")
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
        self.bot.remove_listener(self._on_track_exception, "on_track_exception")
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

        self._load_failures.pop(guild_id, None)

        self.state.record_track_start(guild_id)
        logger.debug("Track started in guild %d: %s", guild_id, track.title)

        await self.ui.spawn_controller(player, track)

    async def _on_track_exception(
        self, event: mafic.TrackExceptionEvent[MusicPlayer]
    ) -> None:
        if event.player.guild.id in self._healing_guilds:
            logger.debug(
                "Ignoring track_exception during healing for guild %s",
                event.player.guild.id,
            )
            return

        player = event.player
        track = event.track
        reason, severity = self._extract_exception_details(event.exception)

        logger.warning(
            "Track exception in guild %s: %s (%s)",
            player.guild.id,
            track.title,
            reason,
        )

        await self._handle_load_failure(
            player,
            track,
            reason=reason,
            severity=severity,
        )

        requester_info = player.get_requester(track)
        payload = TrackExceptionPayload(
            guild_id=player.guild.id,
            track=copy.copy(track),
            reason=reason,
            severity=severity,
            requester_id=requester_info.user_id if requester_info else None,
            channel_id=requester_info.channel_id if requester_info else None,
        )
        self.bot.dispatch("music_track_exception", payload)

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

        if reason is mafic.EndReason.LOAD_FAILED:
            failure_state = self._load_failures.get(player.guild.id)
            expected_id = TrackId.from_track(track).id
            if not (failure_state and failure_state.track_id == expected_id):
                await self._handle_load_failure(
                    player,
                    track,
                    reason="Lavalink: загрузка не удалась",
                    severity=None,
                )

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

    def _extract_exception_details(
        self, exception: LavalinkException
    ) -> tuple[str, str | None]:
        """Extract message and severity from a Lavalink exception payload."""
        message = exception.get("message") or exception.get("cause")
        message_text = str(message) if message else "Неизвестная ошибка"
        severity = exception.get("severity")
        severity_text = str(severity) if severity else None
        return message_text, severity_text

    async def _handle_load_failure(
        self,
        player: MusicPlayer,
        track: mafic.Track,
        *,
        reason: str,
        severity: str | None,
    ) -> None:
        """Handle a track load failure, tracking state and cleaning up UI."""
        guild_id = player.guild.id
        track_id = TrackId.from_track(track).id

        state = self._load_failures.get(guild_id)
        if not state or state.track_id != track_id:
            state = LoadFailureState(
                track_id=track_id,
                reason=reason,
                severity=severity,
            )
            self._load_failures[guild_id] = state
        else:
            if reason and reason != state.reason:
                state.reason = reason
            if severity and not state.severity:
                state.severity = severity

        if state.handled:
            return

        state.handled = True

        await self.ui.controller.destroy_for_guild(guild_id)

        if not state.notified:
            state.notified = True

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

    async def _handle_bot_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> bool:
        bot_user = self.bot.user
        if not bot_user:
            return False

        if member.id != bot_user.id:
            return False

        guild_id = member.guild.id
        if after.channel is None:
            logger.info("Bot was disconnected from guild %s. Cleaning up.", guild_id)
            await self.healer.cleanup_after_disconnect(
                guild_id, is_healing=guild_id in self._healing_guilds
            )
            return True

        if before.channel is not None and before.channel != after.channel:
            logger.info(
                "Bot moved from %s to %s in guild %s. Continuing playback.",
                before.channel.name,
                after.channel.name,
                guild_id,
            )
            if self.state.is_timer_active(guild_id):
                await self._update_channel_timer(guild_id, after.channel)
            return True

        return False

    def _is_relevant_voice_state_update(
        self,
        before: discord.VoiceState,
        after: discord.VoiceState,
        bot_channel: discord.abc.Connectable,
    ) -> bool:
        is_relevant = before.channel == bot_channel or after.channel == bot_channel

        if before.channel == bot_channel == after.channel:
            if before.deaf != after.deaf or before.self_deaf != after.self_deaf:
                is_relevant = True

        return is_relevant

    async def _on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not self.bot.user:
            return

        if await self._handle_bot_voice_state_update(member, before, after):
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

        if not self._is_relevant_voice_state_update(before, after, bot_channel):
            return

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
