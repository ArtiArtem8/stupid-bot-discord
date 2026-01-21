from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
import mafic
from discord.ext import commands

from api.music.models import (
    PLAYER_FAIL_RESULT,
    MusicResult,
    MusicResultStatus,
    PlayResponseData,
    QueueSnapshot,
    RepeatMode,
    RepeatModeData,
    RotateTrackData,
    SkipTrackData,
    VoiceJoinResult,
)
from api.music.service.connection_manager import ConnectionManager
from api.music.service.event_handlers import MusicEventHandlers
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from repositories.volume_repository import VolumeRepository

if TYPE_CHECKING:
    from api.music.player import MusicPlayer

logger = logging.getLogger(__name__)


class CoreMusicService:
    """Core Service facade for the Music module.
    Delegates responsibility to specialized managers.
    """

    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
        volume_repository: VolumeRepository,
        event_handlers: MusicEventHandlers,
        ui_orchestrator: UIOrchestrator,
    ) -> None:
        self.bot = bot
        self.connection = connection_manager
        self.state = state_manager
        self.volume_repo = volume_repository
        self.events = event_handlers
        self.ui = ui_orchestrator
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the service and its components."""
        if self._initialized:
            logger.debug("CoreMusicService already initialized.")
            return

        await self.connection.initialize()
        self.events.setup()
        self._initialized = True
        logger.info("CoreMusicService initialized.")

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Get the music player for a guild."""
        return self.connection.get_player(guild_id)

    async def heal(self, guild_id: int) -> None:
        """Attempt to heal the session for the given guild."""
        await self.events.heal(guild_id)

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        result, old_channel = await self.connection.join(guild, channel)

        if result.status == MusicResultStatus.SUCCESS:
            player = self.connection.get_player(guild.id)
            if player:
                vol = await self.volume_repo.get_volume(guild.id)
                await player.set_volume(vol)

        return result, old_channel

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave voice channel."""
        player = self.connection.get_player(guild.id)

        await self.ui.controller.destroy_for_guild(guild.id)
        await self.end_session(guild.id)
        self.state.cancel_timer(guild.id)

        if not guild.voice_client and (not player or not player.connected):
            return MusicResult(MusicResultStatus.FAILURE, "Not connected")

        if player:
            player.clear_queue()

        await self.connection.disconnect(guild, force=True)
        return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")

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

        player = self.connection.get_player(guild.id)
        if not player:
            return MusicResult(MusicResultStatus.ERROR, "Player not available")

        if text_channel_id:
            session = self.state.get_or_create_session(guild.id)
            session.record_interaction(text_channel_id, requester_id)

        try:
            if not self.connection.pool.nodes:
                await self.connection.initialize()

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
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT

        player.clear_queue()
        await player.stop()
        await self.ui.controller.destroy_for_guild(guild_id)

        if text_channel_id and requester_id:
            msg_session = self.state.get_or_create_session(guild_id)
            msg_session.record_interaction(text_channel_id, requester_id)

        return MusicResult(MusicResultStatus.SUCCESS, "Stopped")

    async def skip(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[SkipTrackData]:
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT

        current = player.current
        up_next = player.queue.next

        await player.skip()
        await player.resume()

        if text_channel_id and requester_id:
            self.state.get_or_create_session(guild_id).record_interaction(
                text_channel_id, requester_id
            )

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Skipped",
            data={"before": current, "after": up_next},
        )

    async def pause(self, guild_id: int) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT
        await player.pause()
        return MusicResult(MusicResultStatus.SUCCESS, "Paused")

    async def resume(self, guild_id: int) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT
        await player.resume()
        return MusicResult(MusicResultStatus.SUCCESS, "Resumed")

    async def shuffle(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT
        player.queue.shuffle()

        if text_channel_id and requester_id:
            self.state.get_or_create_session(guild_id).record_interaction(
                text_channel_id, requester_id
            )

        return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")

    async def rotate(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        player = self.connection.get_player(guild_id)
        if not player or not player.current:
            return MusicResult(MusicResultStatus.FAILURE, "Nothing playing")

        current = player.current
        player.queue.add(current)
        await player.skip()

        if text_channel_id and requester_id:
            self.state.get_or_create_session(guild_id).record_interaction(
                text_channel_id, requester_id
            )

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Rotated",
            data={"skipped": current, "next": player.queue.next},
        )

    async def set_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        from repositories.volume_repository import VolumeData

        await self.volume_repo.save(VolumeData(guild_id=guild_id, volume=volume))
        player = self.connection.get_player(guild_id)
        if player:
            try:
                await player.set_volume(volume)
            except Exception as e:
                logger.warning("Failed to apply volume: %s", e)
                return MusicResult(MusicResultStatus.ERROR, "Failed to apply volume")
        return MusicResult(MusicResultStatus.SUCCESS, "Volume set", data=volume)

    async def get_volume(self, guild_id: int) -> int:
        return await self.volume_repo.get_volume(guild_id)

    async def set_repeat(
        self,
        guild_id: int,
        mode: RepeatMode | None = None,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RepeatModeData]:
        if not (player := self.connection.get_player(guild_id)):
            return PLAYER_FAIL_RESULT

        previous = player.repeat.mode
        if mode is None:
            player.repeat.toggle()
        else:
            player.repeat.mode = mode

        if text_channel_id and requester_id:
            self.state.get_or_create_session(guild_id).record_interaction(
                text_channel_id, requester_id
            )

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Repeat updated",
            data={"mode": player.repeat.mode, "previous": previous},
        )

    async def get_queue(self, guild_id: int) -> MusicResult[QueueSnapshot]:
        player = self.connection.get_player(guild_id)
        if not player or (not player.queue and not player.current):
            return MusicResult(MusicResultStatus.FAILURE, "Queue empty")

        snapshot = QueueSnapshot(
            current=player.current,
            queue=tuple(player.queue),
            repeat_mode=player.repeat.mode,
        )
        return MusicResult(MusicResultStatus.SUCCESS, "Retrieved", data=snapshot)

    async def get_queue_duration(self, guild_id: int) -> int:
        player = self.connection.get_player(guild_id)
        if not player:
            return 0
        total = player.queue.duration
        if player.current:
            position = player.position or 0
            total += max(0, player.current.length - position)
        return total

    async def check_auto_leave(self) -> None:
        """Check for guilds that have been empty for too long."""
        expired_guild_ids = await self.state.check_auto_leave()
        for guild_id in expired_guild_ids:
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self.leave(guild)
            self.state.clear_expired_timers([guild_id])

    async def end_session(self, guild_id: int) -> None:
        """End the music session and dispatch the event."""
        session = self.state.end_session(guild_id)
        if session and session.tracks:
            main_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )
            if main_channel_id:
                self.bot.dispatch(
                    "music_session_end",
                    guild_id,
                    session,
                    main_channel_id,
                )

    async def cleanup(self) -> None:
        """Cleanup on shutdown."""
        for guild in self.bot.guilds:
            if guild.voice_client:
                await guild.voice_client.disconnect(force=True)
        self.events.cleanup()
        self._initialized = False
        logger.info("CoreMusicService cleaned up.")
