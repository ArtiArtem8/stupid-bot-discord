from __future__ import annotations

import asyncio
import logging
import time

import discord
import mafic
from discord.ext import commands

from api.music.errors import compact_external_log_text
from api.music.models import ControllerDestroyReason
from api.music.player import MusicPlayer
from api.music.protocols import HealerProtocol
from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from api.music.session_events import dispatch_music_session_end

logger = logging.getLogger(__name__)

VOICE_TRANSITION_WINDOW_SECONDS = 5.0
VOICE_TRANSITION_VALIDATION_DELAY_SECONDS = 2.0


class VoiceLifecycleHandlers:
    """Handle Discord voice and Mafic node lifecycle events."""

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
        self._recent_voice_transitions: dict[int, float] = {}
        self._voice_transition_validation_tasks: dict[int, asyncio.Task[None]] = {}
        self._unavailable_node_labels: set[str] = set()
        self._setup_done = False

    def setup(self) -> None:
        """Register event listeners."""
        if self._setup_done:
            logger.warning("VoiceLifecycleHandlers setup called multiple times.")
            return

        self.bot.add_listener(self.on_node_ready, "on_node_ready")
        self.bot.add_listener(self.on_node_unavailable, "on_node_unavailable")
        self.bot.add_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.add_listener(self._on_websocket_closed, "on_websocket_closed")
        self._setup_done = True

    def cleanup(self) -> None:
        """Remove event listeners."""
        if not self._setup_done:
            return

        self.bot.remove_listener(self.on_node_ready, "on_node_ready")
        self.bot.remove_listener(self.on_node_unavailable, "on_node_unavailable")
        self.bot.remove_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.remove_listener(self._on_websocket_closed, "on_websocket_closed")
        for task in self._voice_transition_validation_tasks.values():
            task.cancel()
        self._voice_transition_validation_tasks.clear()
        self._recent_voice_transitions.clear()
        self._unavailable_node_labels.clear()
        self._setup_done = False
        logger.info("VoiceLifecycleHandlers listeners removed.")

    async def on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
        self._unavailable_node_labels.discard(node.label)
        logger.info("Lavalink node '%s' is ready", node.label)

    async def on_node_unavailable(self, node: mafic.Node[commands.Bot]) -> None:
        players: list[MusicPlayer] = []
        for player in node.players:
            if isinstance(player, MusicPlayer):
                players.append(player)
        affected_guild_ids: set[int] = {player.guild.id for player in players}
        if node.label not in self._unavailable_node_labels:
            message_format = (
                "Lavalink node unavailable node=%s node_available=%s "
                + "affected_guilds=%s"
            )
            logger.warning(
                message_format,
                node.label,
                node.available,
                sorted(affected_guild_ids),
            )
            self._unavailable_node_labels.add(node.label)

        invalidated_guild_ids = await self.connection.handle_node_unavailable(node)
        await self._cleanup_after_node_unavailable(invalidated_guild_ids)

    async def _cleanup_after_node_unavailable(
        self,
        affected_guild_ids: set[int],
    ) -> None:
        for guild_id in affected_guild_ids:
            await self.ui.controller.destroy_for_guild(
                guild_id,
                ControllerDestroyReason.PLAYER_ERROR,
            )
            self.state.cancel_timer(guild_id)

    async def _on_websocket_closed(
        self, event: mafic.WebSocketClosedEvent[MusicPlayer]
    ) -> None:
        if not self._should_handle_player_event(event.player, "websocket_closed"):
            return

        guild_id = event.player.guild.id
        reason = compact_external_log_text(event.reason)
        message_format = (
            "Discord voice websocket failure guild=%s code=%s "
            + "by_discord=%s reason=%r"
        )
        logger.warning(
            message_format,
            guild_id,
            event.code,
            event.by_discord,
            reason,
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

        if event.code == 4014 and event.by_discord:
            await self._cleanup_after_disconnect(guild_id, player=event.player)
            return

        await self.ui.controller.destroy_for_guild(
            guild_id, ControllerDestroyReason.VOICE_DISCONNECT
        )

    def _should_handle_player_event(
        self,
        player: MusicPlayer,
        event_name: str,
    ) -> bool:
        guild_id = player.guild.id
        if guild_id in self._healing_guilds or not self.connection.is_current_player(
            player
        ):
            logger.debug(
                "Ignoring %s from non-current or healing player for guild %s",
                event_name,
                guild_id,
            )
            return False
        return True

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
                await self.connection.invalidate_player(
                    event_player,
                    context="voice_transition_validation",
                )

        except asyncio.CancelledError:
            logger.debug(
                "Voice transition validation cancelled for guild %s",
                guild_id,
            )
            raise
        except Exception:
            logger.exception(
                "Voice transition validation failed for guild %s",
                guild_id,
            )
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

    def is_healing(self, guild_id: int) -> bool:
        """Return whether reconstructive recovery owns this guild."""
        return guild_id in self._healing_guilds

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
            await self._cleanup_after_disconnect(
                guild_id,
                player=(
                    member.guild.voice_client
                    if isinstance(member.guild.voice_client, MusicPlayer)
                    else None
                ),
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

    async def _cleanup_after_disconnect(
        self,
        guild_id: int,
        *,
        player: MusicPlayer | None = None,
    ) -> None:
        """Finalize application state after a real Discord voice disconnect."""
        await self.ui.controller.destroy_for_guild(
            guild_id, ControllerDestroyReason.VOICE_DISCONNECT
        )
        self.state.cancel_timer(guild_id)
        if guild_id in self._healing_guilds:
            return

        session = self.state.end_session(guild_id)
        dispatch_music_session_end(self.bot, guild_id, session)
        if player is not None:
            player.clear_queue()

    def _is_relevant_voice_state_update(
        self,
        before: discord.VoiceState,
        after: discord.VoiceState,
        bot_channel: discord.abc.Connectable,
    ) -> bool:
        return bot_channel in (before.channel, after.channel)

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

        if not voice_client or not isinstance(voice_client, MusicPlayer):
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
        elif self.state.is_timer_active(guild_id):
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
