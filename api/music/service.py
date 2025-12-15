"""Music Service Layer."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TypedDict, cast

import discord
import mafic
from discord.ext import commands
from discord.utils import utcnow

import config
from repositories.volume_repository import VolumeRepository
from utils.json_utils import get_json, save_json

from .healer import SessionHealer
from .models import (
    ControllerManagerProtocol,
    MusicResult,
    MusicResultStatus,
    MusicSession,
    NodeNotConnectedError,
    PlayResponseData,
    QueueSnapshot,
    RepeatModeData,
    RotateTrackData,
    SkipTrackData,
    VoiceCheckResult,
    VoiceJoinResult,
)
from .player import MusicPlayer, music_player_factory
from .service.connection_manager import ConnectionManager
from .service.state_manager import StateManager
from .service.ui_orchestrator import UIOrchestrator

logger = logging.getLogger(__name__)


class EmptyTimerInfo(TypedDict):
    timestamp: float
    reason: str | None


class MusicService:
    """Service for managing music playback."""

    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: "ConnectionManager",
        state_manager: "StateManager",
        volume_repository: "VolumeRepository",
        ui_orchestrator: "UIOrchestrator",
        controller_manager: "ControllerManagerProtocol",
    ) -> None:
        self.bot = bot
        self.pool = mafic.NodePool(bot)
        self.sessions: dict[int, MusicSession] = {}
        self._track_start_times: dict[int, datetime] = {}
        self._initialized = False

        # Recover mechanic
        self.healer = SessionHealer(
            bot=bot,
            connection_manager=connection_manager,
            state_manager=state_manager,
            volume_repository=volume_repository,
            ui_orchestrator=ui_orchestrator,
        )
        self._healing_guilds: set[int] = set()

        # Auto-leave tracking
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}
        self.controller_manager = controller_manager

    async def initialize(self) -> None:
        """Initialize Lavalink node connection."""
        if self._initialized:
            return

        try:
            await self.pool.create_node(
                host=config.LAVALINK_HOST,
                port=config.LAVALINK_PORT,
                password=config.LAVALINK_PASSWORD,
                label="MAIN",
                secure=getattr(config, "LAVALINK_SECURE", False),
            )
            self._initialized = True
            self._setup_event_listeners()

            self._setup_event_listeners()

            logger.info("Mafic node pool initialized successfully")

        except Exception as e:
            logger.exception("Failed to initialize Mafic node")
            raise NodeNotConnectedError(f"Failed to connect: {e}") from e

    def _setup_event_listeners(self) -> None:
        """Register event listeners with the bot."""
        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_node_ready, "on_node_ready")
        self.bot.add_listener(self._on_voice_state_update, "on_voice_state_update")
        self.bot.add_listener(self._on_websocket_closed, "on_websocket_closed")

    async def _on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
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
        self.sessions.setdefault(guild_id, MusicSession(guild_id=guild_id))
        self._track_start_times[guild_id] = utcnow()
        logger.debug("Track started in guild %d: %s", guild_id, track.title)

        await self._spawn_controller(player, track)

    async def _spawn_controller(self, player: MusicPlayer, track: mafic.Track) -> None:
        """Helper to safely spawn a UI controller."""
        requester_info = player.get_requester(track)
        if not requester_info:
            logger.debug("No requester found for track: %s", track.title)
            return

        # Determine best channel: Explicit > Most Used
        channel_id = requester_info.channel_id
        if not channel_id:
            session = self.sessions.get(player.guild.id)
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

        if track.length < 45_000:  # TODO: detect if live stream
            logger.debug("Track too short: %s, %s", track.title, track.length)
            return

        await self.controller_manager.create_for_user(
            guild_id=player.guild.id,
            user_id=requester_info.user_id,
            channel=channel,
            player=player,
            track=track,
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

        await self._record_history(player, track, reason)

        if event.reason is mafic.EndReason.FINISHED and player.queue.is_empty:
            logger.debug("Queue finished. destroying controller immediately.")
            await self.controller_manager.destroy_for_guild(player.guild.id)

        if reason in (mafic.EndReason.FINISHED, mafic.EndReason.LOAD_FAILED):
            await player.advance(previous_track=track)

    async def _record_history(
        self, player: MusicPlayer, track: mafic.Track, reason: mafic.EndReason
    ) -> None:
        guild_id = player.guild.id
        session = self.sessions.get(guild_id)
        start_time = self._track_start_times.pop(guild_id, None)

        if not session or not start_time:
            return

        skipped = False
        if reason == mafic.EndReason.STOPPED:
            skipped = True
        elif reason == mafic.EndReason.REPLACED:
            skipped = True

        requester_info = player.get_requester(track)

        session.add_track(
            title=track.title,
            uri=track.uri or "",
            requester_id=requester_info.user_id if requester_info else None,
            channel_id=requester_info.channel_id if requester_info else None,
            skipped=skipped,
            timestamp=start_time,
        )
        logger.debug("Recorded history: %s (Skipped: %s)", track.title, skipped)

    async def _on_websocket_closed(
        self, event: mafic.WebSocketClosedEvent[MusicPlayer]
    ) -> None:
        """Handle Lavalink/Discord voice websocket closures.

        - 1000: Normal closure (usually ignored)
        - 4006: Session invalid (force disconnect, region change fail, etc.)
        - 4014: Disconnected can be normal move, but if by_discord=True often means kick
        """
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
            # Normal cleanup
            await self._cleanup_after_disconnect(event.player.guild.id)

    async def heal(self, guild_id: int) -> None:
        if guild_id in self._healing_guilds:
            return
        self._healing_guilds.add(guild_id)
        try:
            await self.controller_manager.destroy_for_guild(guild_id)
            self.empty_channel_timers.pop(guild_id, None)

            await self.healer.capture_and_heal(guild_id)
        finally:
            self._healing_guilds.discard(guild_id)

    async def _on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Monitor voice state changes for:
        1. Bot disconnection (Graceful Cleanup)
        2. Bot movement (Update State)
        3. Auto-leave (Empty Channel detection).
        """
        if not self.bot.user:
            return

        if member.id == self.bot.user.id:
            if after.channel is None:
                logger.info(
                    "Bot was disconnected from guild %s. Cleaning up.", member.guild.id
                )
                # We do NOT call self.leave() here because we are already disconnected.
                # Just clean up the internal state.
                await self._cleanup_after_disconnect(member.guild.id)
                return

            if before.channel is not None and before.channel != after.channel:
                logger.info(
                    "Bot moved from %s to %s in guild %s. Continuing playback.",
                    before.channel.name,
                    after.channel.name,
                    member.guild.id,
                )
                if member.guild.id in self.empty_channel_timers:
                    await self._update_channel_timer(member.guild.id, after.channel)
                return

        guild = member.guild
        voice_client = guild.voice_client

        # Guard: Bot must be connected
        if not voice_client or not isinstance(voice_client, MusicPlayer):
            return

        bot_channel = voice_client.channel
        if not bot_channel:
            return

        # Check if update affects the bot's current channel
        is_relevant = before.channel == bot_channel or after.channel == bot_channel

        # Also check deafen state if member is in the same channel
        if before.channel == bot_channel == after.channel:
            if before.deaf != after.deaf or before.self_deaf != after.self_deaf:
                is_relevant = True

        if is_relevant:
            if isinstance(bot_channel, (discord.VoiceChannel, discord.StageChannel)):
                await self._update_channel_timer(guild.id, bot_channel)

    async def _cleanup_after_disconnect(self, guild_id: int) -> None:
        """Cleans up session/controller state without calling player.disconnect()."""
        # 1. Destroy Controller (UI)
        await self.controller_manager.destroy_for_guild(guild_id)

        # 2. End Session (Triggers history embed)
        await self.end_session(guild_id)

        # 3. Clear Timer
        self.empty_channel_timers.pop(guild_id, None)

        # 4. Clear Internal State
        player = self.get_player(guild_id)
        if player:
            player.clear_queue()

    async def _update_channel_timer(
        self, guild_id: int, channel: discord.VoiceChannel | discord.StageChannel
    ) -> None:
        """Update the empty channel timer for a specific guild."""
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
            if guild_id not in self.empty_channel_timers:
                logger.info(
                    "Channel %s in guild %s is effectively empty (%s). Starting timer.",
                    channel.name,
                    guild_id,
                    empty_reason,
                )
                self.empty_channel_timers[guild_id] = EmptyTimerInfo(
                    timestamp=time.monotonic(),
                    reason=empty_reason,
                )
        else:
            if guild_id in self.empty_channel_timers:
                logger.info(
                    "Channel %s in guild %s is no longer empty. Cancelling timer.",
                    channel.name,
                    guild_id,
                )
                self.empty_channel_timers.pop(guild_id, None)

    async def check_auto_leave(self) -> None:
        """Check for guilds that have been empty for too long."""
        current_time = time.monotonic()
        timeout_duration = config.MUSIC_AUTO_LEAVE_TIMEOUT

        for guild_id, info in list(self.empty_channel_timers.items()):
            if current_time - info["timestamp"] > timeout_duration:
                await self._auto_leave_guild(guild_id, info["reason"])

    async def _auto_leave_guild(self, guild_id: int, reason: str | None) -> None:
        """Handle the actual leaving logic."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            self.empty_channel_timers.pop(guild_id, None)
            return

        # If somehow we are not connected, cleanup
        if not guild.voice_client:
            self.empty_channel_timers.pop(guild_id, None)
            return

        try:
            logger.info(
                "Auto-leaving guild %s (%s) due to inactivity (%s).",
                guild.name,
                guild_id,
                reason,
            )
            await self.leave(guild)
            self.empty_channel_timers.pop(guild_id, None)
        except Exception as e:
            logger.error("Failed to auto-leave guild %s: %s", guild_id, e)

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            return guild.voice_client
        return None

    async def get_volume(self, guild_id: int) -> int:
        data = get_json(config.MUSIC_VOLUME_FILE) or {}
        return data.get(str(guild_id), config.MUSIC_DEFAULT_VOLUME)

    async def save_volume(self, guild_id: int, volume: int) -> None:
        data = get_json(config.MUSIC_VOLUME_FILE) or {}
        data[str(guild_id)] = volume
        save_json(config.MUSIC_VOLUME_FILE, data)

    async def _record_interaction(
        self, guild_id: int, text_channel_id: int | None, requester_id: int | None
    ) -> None:
        if text_channel_id and requester_id:
            session = self.sessions.setdefault(
                guild_id, MusicSession(guild_id=guild_id)
            )
            session.record_interaction(text_channel_id, requester_id)

    # --- Actions ---

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        logger.debug("Joining channel: %s", channel)

        voice_client = guild.voice_client

        if (
            voice_client
            and isinstance(
                voice_client.channel, (discord.VoiceChannel, discord.StageChannel)
            )
            and voice_client.channel.id == channel.id
        ):
            return VoiceCheckResult.ALREADY_CONNECTED, None

        try:
            if voice_client and isinstance(voice_client, MusicPlayer):
                old_channel = cast(discord.abc.GuildChannel, voice_client.channel)
                await voice_client.move_to(channel)
                return VoiceCheckResult.MOVED_CHANNELS, old_channel

            await channel.connect(cls=music_player_factory)

            player = self.get_player(guild.id)
            if player:
                vol = await self.get_volume(guild.id)
                await player.set_volume(vol)

            # Initial check for channel state
            await self._update_channel_timer(guild.id, channel)

            return VoiceCheckResult.SUCCESS, None

        except asyncio.TimeoutError:
            logger.warning("Voice connection timed out for guild %s", guild.id)
            return VoiceCheckResult.CONNECTION_FAILED, None
        except Exception:
            logger.exception("Failed to join voice channel")
            return VoiceCheckResult.CONNECTION_FAILED, None

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        player = self.get_player(guild.id)
        voice_client = guild.voice_client

        # Even if player is None, we might have lingering session state to clean up
        is_player_connected = player and player.connected
        is_voice_connected = (
            voice_client
            and isinstance(voice_client, MusicPlayer)
            and voice_client.is_connected()
        )

        try:
            # Always clean up session/UI first (Idempotent)
            await self.end_session(guild.id)
            await self.controller_manager.destroy_for_guild(guild.id)
            self.empty_channel_timers.pop(guild.id, None)

            if not is_player_connected and not is_voice_connected:
                return MusicResult(MusicResultStatus.FAILURE, "Not connected")
            if player:
                player.clear_queue()
                if player.connected:
                    await player.disconnect()
            if guild.voice_client:
                await guild.voice_client.disconnect(force=True)

            return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")
        except Exception as e:
            logger.exception("Error leaving voice")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def end_session(self, guild_id: int) -> None:
        session = self.sessions.pop(guild_id, None)
        logger.debug("Ending session for guild %s, %s", guild_id, session)
        self._track_start_times.pop(guild_id, None)

        if session and session.tracks:
            main_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )
            if main_channel_id and guild_id not in self._healing_guilds:
                self.bot.dispatch(
                    "music_session_end", guild_id, session, main_channel_id
                )

    async def play(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        query: str,
        requester_id: int,
        text_channel_id: int | None = None,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        check_result, old_channel = await self.join(guild, voice_channel)
        if check_result.status is MusicResultStatus.ERROR:
            return MusicResult(
                check_result.status,
                "Connection failed",
                data=(check_result, old_channel),
            )

        player = self.get_player(guild.id)
        if not player:
            return MusicResult(MusicResultStatus.ERROR, "Player not available")

        await self._record_interaction(guild.id, text_channel_id, requester_id)

        try:
            if not self.pool.nodes:
                await self.initialize()

            result = await player.fetch_tracks(query)

            if not result:
                return MusicResult(MusicResultStatus.FAILURE, "Nothing found")

            if isinstance(result, mafic.Playlist):
                for track in result.tracks:
                    player.set_requester(track, requester_id, text_channel_id)
                player.queue.add(result.tracks)
                if not player.current:
                    await player.advance()

                return MusicResult(
                    MusicResultStatus.SUCCESS,
                    "Playlist added",
                    data={"type": "playlist", "playlist": result},
                )

            track = result[0]
            player.set_requester(track, requester_id, text_channel_id)

            player.queue.add(track)

            is_playing_before = player.current is not None

            if not is_playing_before:
                await player.advance()

            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Track processed",
                data={"type": "track", "track": track, "playing": is_playing_before},
            )

        except Exception as e:
            logger.exception("Error in play")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def stop(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        player.clear_queue()
        await player.stop()
        await self.controller_manager.destroy_for_guild(guild_id)
        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(MusicResultStatus.SUCCESS, "Stopped")

    async def skip(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[SkipTrackData]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        current = player.current
        up_next = player.queue.next

        await player.skip()
        await player.resume()
        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Skipped",
            data={"before": current, "after": up_next},
        )

    async def pause(self, guild_id: int) -> MusicResult[None]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")
        await player.pause()
        return MusicResult(MusicResultStatus.SUCCESS, "Paused")

    async def resume(self, guild_id: int) -> MusicResult[None]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")
        await player.resume()
        return MusicResult(MusicResultStatus.SUCCESS, "Resumed")

    async def shuffle(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")
        player.queue.shuffle()
        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")

    async def rotate(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        player = self.get_player(guild_id)
        if not player or not player.current:
            return MusicResult(MusicResultStatus.FAILURE, "Nothing playing")

        current = player.current
        player.queue.add(current)
        await player.skip()

        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Rotated",
            data={"skipped": current, "next": player.queue.next},
        )

    async def set_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        await self.save_volume(guild_id, volume)
        player = self.get_player(guild_id)
        if player:
            try:
                await player.set_volume(volume)
            except Exception as e:
                logger.warning("Failed to apply volume: %s", e)
                return MusicResult(MusicResultStatus.ERROR, "Failed to apply volume")
        return MusicResult(MusicResultStatus.SUCCESS, "Volume set", data=volume)

    async def set_repeat(
        self,
        guild_id: int,
        mode: str | None = None,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RepeatModeData]:
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        previous = player.repeat.mode
        if mode is None:
            player.repeat.toggle()
        else:
            player.repeat.mode = mode  # type: ignore

        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Repeat updated",
            data={"mode": player.repeat.mode, "previous": previous},
        )

    async def get_queue(self, guild_id: int) -> MusicResult[QueueSnapshot]:
        player = self.get_player(guild_id)
        if not player or (not player.queue and not player.current):
            return MusicResult(MusicResultStatus.FAILURE, "Queue empty")

        snapshot = QueueSnapshot(
            current=player.current,
            queue=tuple(player.queue.tracks),
            repeat_mode=player.repeat.mode,
        )
        return MusicResult(MusicResultStatus.SUCCESS, "Retrieved", data=snapshot)

    async def get_queue_duration(self, guild_id: int) -> int:
        player = self.get_player(guild_id)
        if not player:
            return 0
        total = player.queue.duration_ms
        if player.current:
            position = player.position or 0
            total += max(0, player.current.length - position)
        return total

    async def cleanup(self) -> None:
        """Cleanup on shutdown."""
        for guild in self.bot.guilds:
            if guild.voice_client:
                await guild.voice_client.disconnect(force=True)
