from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import TYPE_CHECKING

import discord
import mafic
from discord.ext import commands
from mafic.typings import LavalinkException

from api.music.models import ControllerDestroyReason, TrackExceptionPayload, TrackId
from api.music.protocols import HealerProtocol
from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator

if TYPE_CHECKING:
    from api.music.player import MusicPlayer

logger = logging.getLogger(__name__)

VOICE_TRANSITION_WINDOW_SECONDS = 5.0
VOICE_TRANSITION_VALIDATION_DELAY_SECONDS = 2.0


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
        self._load_failures: dict[int, TrackId] = {}
        self._recent_voice_transitions: dict[int, float] = {}
        self._voice_transition_validation_tasks: dict[int, asyncio.Task[None]] = {}
        self._unavailable_node_labels: set[str] = set()
        self._setup_done = False

    def setup(self) -> None:
        """Register event listeners."""
        if self._setup_done:
            logger.warning("MusicEventHandlers setup called multiple times.")
            return

        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_track_exception, "on_track_exception")
        self.bot.add_listener(self._on_track_stuck, "on_track_stuck")
        self.bot.add_listener(self.on_node_ready, "on_node_ready")
        self.bot.add_listener(self.on_node_unavailable, "on_node_unavailable")
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
        self.bot.remove_listener(self._on_track_stuck, "on_track_stuck")
        self.bot.remove_listener(self.on_node_ready, "on_node_ready")
        self.bot.remove_listener(self.on_node_unavailable, "on_node_unavailable")
        self.bot.remove_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.remove_listener(self._on_websocket_closed, "on_websocket_closed")
        for task in self._voice_transition_validation_tasks.values():
            task.cancel()
        self._voice_transition_validation_tasks.clear()
        self._load_failures.clear()
        self._recent_voice_transitions.clear()
        self._unavailable_node_labels.clear()
        self._setup_done = False
        logger.info("MusicEventHandlers listeners removed.")

    async def on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
        self._unavailable_node_labels.discard(node.label)
        logger.info("Lavalink node '%s' is ready", node.label)

    async def on_node_unavailable(self, node: mafic.Node[commands.Bot]) -> None:
        if node.label not in self._unavailable_node_labels:
            logger.warning("Lavalink node '%s' became unavailable", node.label)
            self._unavailable_node_labels.add(node.label)

        await self.connection.mark_node_unavailable(node)
        await self._cleanup_after_node_unavailable()

    async def _cleanup_after_node_unavailable(self) -> None:
        for guild in self.bot.guilds:
            self._load_failures.pop(guild.id, None)
            await self.ui.controller.destroy_for_guild(
                guild.id,
                ControllerDestroyReason.PLAYER_ERROR,
            )
            self.state.cancel_timer(guild.id)

            voice_client = guild.voice_client
            if not isinstance(voice_client, mafic.Player):
                continue

            await self.connection.detach_stale_voice_client(guild, voice_client)  # pyright: ignore[reportUnknownArgumentType]

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

        track_id = TrackId.from_track(track)
        if not self._claim_load_failure(player.guild.id, track_id):
            return

        self._dispatch_track_exception(player, track, reason, severity)
        await self.ui.controller.destroy_for_guild(
            player.guild.id,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_track_id=track_id,
        )

    async def _on_track_stuck(self, event: mafic.TrackStuckEvent[MusicPlayer]) -> None:
        """Remove stale controls when Lavalink reports a stalled track.

        During healing, stuck events from the old or restoring player should not drive
        the normal controller lifecycle.
        """
        guild_id = event.player.guild.id

        if guild_id in self._healing_guilds:
            logger.debug(
                "Ignoring track_stuck during healing for guild %s",
                guild_id,
            )
            return

        logger.warning(
            "Track stuck in guild %s: %s",
            guild_id,
            event.track.title,
        )
        await self.ui.controller.destroy_for_guild(
            guild_id,
            ControllerDestroyReason.TRACK_STUCK,
            expected_track_id=TrackId.from_track(event.track),
        )

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

        track_id = TrackId.from_track(track)
        if reason is mafic.EndReason.LOAD_FAILED and self._claim_load_failure(
            player.guild.id,
            track_id,
        ):
            self._dispatch_track_exception(
                player,
                track,
                reason="Lavalink: загрузка не удалась",
                severity=None,
            )

        await self.ui.controller.destroy_for_guild(
            player.guild.id,
            ControllerDestroyReason.TRACK_END,
            expected_track_id=track_id,
        )

        if reason is mafic.EndReason.LOAD_FAILED:
            self._clear_load_failure(player.guild.id, track_id)

        if reason in (mafic.EndReason.FINISHED, mafic.EndReason.LOAD_FAILED):
            await player.advance_after_end(track)
        elif reason is mafic.EndReason.STOPPED:
            await player.start_queued_if_idle()

    def _extract_exception_details(
        self, exception: LavalinkException
    ) -> tuple[str, str | None]:
        """Extract message and severity from a Lavalink exception payload."""
        message = exception.get("message") or exception.get("cause")
        message_text = str(message) if message else "Неизвестная ошибка"
        severity = exception.get("severity")
        severity_text = str(severity) if severity else None
        return message_text, severity_text

    def _claim_load_failure(self, guild_id: int, track_id: TrackId) -> bool:
        """Claim the active load failure for this guild and track."""
        if self._load_failures.get(guild_id) == track_id:
            return False

        self._load_failures[guild_id] = track_id
        return True

    def _clear_load_failure(self, guild_id: int, track_id: TrackId) -> None:
        """Clear the active load failure only if it still matches this track."""
        if self._load_failures.get(guild_id) == track_id:
            self._load_failures.pop(guild_id, None)

    def _dispatch_track_exception(
        self,
        player: MusicPlayer,
        track: mafic.Track,
        reason: str,
        severity: str | None,
    ) -> None:
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
            return

        if self._has_recent_voice_transition(guild_id):
            msg = (
                "Deferring websocket cleanup during voice transition for guild %s "
                "(code=%s, reason=%s, by_discord=%s)."
            )
            logger.debug(
                msg,
                guild_id,
                event.code,
                event.reason,
                event.by_discord,
            )
            self._schedule_voice_transition_validation(guild_id, event.player)
            return

        await self.ui.controller.destroy_for_guild(
            guild_id, ControllerDestroyReason.VOICE_DISCONNECT
        )

        if event.code == 4014 and event.by_discord:
            await self.healer.cleanup_after_disconnect(event.player.guild.id)

    def _has_recent_voice_transition(self, guild_id: int) -> bool:
        transition_at = self._recent_voice_transitions.get(guild_id)
        if transition_at is None:
            return False
        if time.monotonic() - transition_at <= VOICE_TRANSITION_WINDOW_SECONDS:
            return True
        self._recent_voice_transitions.pop(guild_id, None)
        return False

    def _schedule_voice_transition_validation(
        self, guild_id: int, event_player: MusicPlayer
    ) -> None:
        previous = self._voice_transition_validation_tasks.get(guild_id)
        if previous and not previous.done():
            previous.cancel()

        task = asyncio.create_task(
            self._validate_voice_transition_recovery(guild_id, event_player)
        )
        self._voice_transition_validation_tasks[guild_id] = task

    async def _validate_voice_transition_recovery(
        self, guild_id: int, event_player: MusicPlayer
    ) -> None:
        try:
            await asyncio.sleep(VOICE_TRANSITION_VALIDATION_DELAY_SECONDS)

            player = self.connection.get_player(guild_id)
            if player and player.connected and player.channel and player.current:
                logger.debug(
                    "Voice transition recovered in guild %s; preserving controller.",
                    guild_id,
                )
                return

            logger.warning(
                "Voice transition did not recover in guild %s; destroying controller.",
                guild_id,
            )
            await self.ui.controller.destroy_for_guild(
                guild_id, ControllerDestroyReason.VOICE_DISCONNECT
            )

            if not self.connection.is_player_usable(event_player):
                await self.connection.detach_stale_voice_client(
                    event_player.guild,
                    event_player,
                )

        except asyncio.CancelledError:
            logger.debug(
                "Voice transition validation cancelled for guild %s",
                guild_id,
            )
            raise
        finally:
            self._recent_voice_transitions.pop(guild_id, None)
            current_task = asyncio.current_task()
            if self._voice_transition_validation_tasks.get(guild_id) is current_task:
                self._voice_transition_validation_tasks.pop(guild_id, None)

    async def heal(self, guild_id: int) -> bool:
        if guild_id in self._healing_guilds:
            return False

        self._healing_guilds.add(guild_id)
        try:
            await self.ui.controller.destroy_for_guild(
                guild_id,
                ControllerDestroyReason.PLAYER_ERROR,
            )
            self.state.cancel_timer(guild_id)
            return await self.healer.capture_and_heal(guild_id)
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
            self._load_failures.pop(guild_id, None)
            await self.healer.cleanup_after_disconnect(
                guild_id, is_healing=guild_id in self._healing_guilds
            )
            return True

        if before.channel is not None and before.channel != after.channel:
            self._recent_voice_transitions[guild_id] = time.monotonic()
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
        empty_reason = self._empty_channel_reason(channel)
        if empty_reason is not None:
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

    def _empty_channel_reason(
        self, channel: discord.VoiceChannel | discord.StageChannel
    ) -> str | None:
        human_members = [member for member in channel.members if not member.bot]
        if not human_members:
            return "empty"
        if all(
            member.voice.self_deaf or member.voice.deaf
            for member in human_members
            if member.voice is not None
        ):
            return "all_deafened"
        return None
