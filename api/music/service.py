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
from utils.json_utils import get_json, save_json

from .models import (
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

LOGGER = logging.getLogger(__name__)


class EmptyTimerInfo(TypedDict):
    timestamp: float
    reason: str | None


class MusicService:
    """Service for managing music playback."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pool = mafic.NodePool(bot)
        self.sessions: dict[int, MusicSession] = {}
        self._track_start_times: dict[int, datetime] = {}
        self._initialized = False

        # Auto-leave tracking
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}

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

            LOGGER.info("Mafic node pool initialized successfully")

        except Exception as e:
            LOGGER.exception("Failed to initialize Mafic node")
            raise NodeNotConnectedError(f"Failed to connect: {e}") from e

    def _setup_event_listeners(self) -> None:
        """Register event listeners with the bot."""
        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_node_ready, "on_node_ready")
        self.bot.add_listener(self._on_voice_state_update, "on_voice_state_update")

    async def _on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
        LOGGER.info("Lavalink node '%s' is ready", node.label)

    async def _on_track_start(self, event: mafic.TrackStartEvent[MusicPlayer]) -> None:
        guild_id = event.player.guild.id
        self.sessions.setdefault(guild_id, MusicSession(guild_id=guild_id))
        self._track_start_times[guild_id] = utcnow()
        LOGGER.debug("Track started in guild %d: %s", guild_id, event.track.title)

    async def _on_track_end(self, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
        player = event.player
        guild_id = player.guild.id
        track = event.track
        reason = event.reason

        LOGGER.debug(
            "Track ended in guild %d: %s (reason: %s)", guild_id, track.title, reason
        )

        session = self.sessions.get(guild_id)
        start_time = self._track_start_times.pop(guild_id, None)

        if session and start_time:
            elapsed = (utcnow() - start_time).total_seconds()
            LOGGER.debug(
                "Track '%s' in guild %d played for %.2fs.",
                track.title,
                guild_id,
                elapsed,
            )
            skipped = elapsed < 20 and reason in (
                mafic.EndReason.STOPPED,
                mafic.EndReason.REPLACED,
            )
            requester_info = player.get_requester(track)
            LOGGER.debug("Requester info: %s", player._requester_map)  # type: ignore
            if not requester_info:
                LOGGER.warning("No requester info for track %s", track.title)
            user_id = requester_info.user_id if requester_info else None
            channel_id = requester_info.channel_id if requester_info else None
            session.add_track(
                track.title,
                track.uri or "",
                user_id,
                channel_id,
                skipped,
            )

        if reason in (mafic.EndReason.FINISHED, mafic.EndReason.LOAD_FAILED):
            await player.advance(previous_track=event.track)

    async def _on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Monitor voice state changes for auto-leave feature."""
        if not self.bot.user:
            return

        guild = member.guild
        if not guild.voice_client or not isinstance(guild.voice_client, MusicPlayer):
            return

        bot_channel = guild.voice_client.channel
        if not bot_channel:
            return

        # Check if the update affects the bot's channel
        affected = False
        if before.channel == bot_channel or after.channel == bot_channel:
            affected = True

        # Also check for deafen status changes if in the same channel
        if before.channel == bot_channel == after.channel and (
            before.deaf != after.deaf or before.self_deaf != after.self_deaf
        ):
            affected = True

        if affected and isinstance(
            bot_channel, (discord.VoiceChannel, discord.StageChannel)
        ):
            await self._update_channel_timer(guild.id, bot_channel)

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
                LOGGER.info(
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
                LOGGER.info(
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
            LOGGER.info(
                "Auto-leaving guild %s (%s) due to inactivity (%s).",
                guild.name,
                guild_id,
                reason,
            )
            await self.leave(guild)
            self.empty_channel_timers.pop(guild_id, None)
        except Exception as e:
            LOGGER.error("Failed to auto-leave guild %s: %s", guild_id, e)

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
        LOGGER.debug("Joining channel: %s", channel)

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
            LOGGER.warning("Voice connection timed out for guild %s", guild.id)
            return VoiceCheckResult.CONNECTION_FAILED, None
        except Exception:
            LOGGER.exception("Failed to join voice channel")
            return VoiceCheckResult.CONNECTION_FAILED, None

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        player = self.get_player(guild.id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "Not connected")

        try:
            await self.end_session(guild.id)
            player.clear_queue()
            await player.disconnect()

            # Clear timer on leave
            self.empty_channel_timers.pop(guild.id, None)

            return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")
        except Exception as e:
            LOGGER.exception("Error leaving voice")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def end_session(self, guild_id: int) -> None:
        session = self.sessions.pop(guild_id, None)
        self._track_start_times.pop(guild_id, None)

        if session and session.tracks:
            main_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )
            if main_channel_id:
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
            LOGGER.exception("Error in play")
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
                LOGGER.warning("Failed to apply volume: %s", e)
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
