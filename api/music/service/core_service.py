from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

import discord
import mafic
from discord.ext import commands

from api.music.errors import EXPECTED_LAVALINK_IO_ERRORS, classify_music_exception
from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    ControllerDestroyReason,
    MusicResult,
    MusicResultStatus,
    NodeNotConnectedError,
    PlayResponseData,
    QueueSnapshot,
    RepeatMode,
    RepeatModeData,
    RotateTrackData,
    SkipTrackData,
    TrackId,
    VoiceCheckResult,
    VoiceJoinResult,
    player_fail_result,
)
from api.music.player import MusicPlayer
from api.music.service.connection_manager import ConnectionManager
from api.music.service.event_handlers import MusicEventHandlers
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from api.music.session_events import dispatch_music_session_end
from repositories.volume_repository import VolumeRepository

logger = logging.getLogger(__name__)

T = TypeVar("T")
EXPECTED_PLAY_ERRORS = (
    mafic.TrackLoadException,
    mafic.PlayerNotConnected,
    mafic.PlayerException,
    NodeNotConnectedError,
)


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

        self.events.setup()
        self._initialized = True
        self.connection.start_lazy_connect()
        logger.info("CoreMusicService initialized.")

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Get the music player for a guild."""
        return self.connection.get_player(guild_id)

    async def heal(self, guild_id: int) -> bool:
        """Attempt to heal the session for the given guild."""
        return await self.events.heal(guild_id)

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        result, old_channel = await self.connection.join(guild, channel)

        if result.status == MusicResultStatus.SUCCESS:
            player = self.connection.get_player(guild.id)
            if player:
                vol = await self.volume_repo.get_volume(guild.id)
                try:
                    await self._apply_volume(player, vol)
                except EXPECTED_LAVALINK_IO_ERRORS as exc:
                    await self._handle_player_io_failure(player, exc)
                    return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None

        return result, old_channel

    async def _apply_volume(self, player: MusicPlayer, volume: int) -> None:
        """Apply volume with a short retry while the voice connection stabilizes."""
        retry_delays = (0.0, 0.3, 0.6)
        for delay in retry_delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                if player.connected:
                    await player.set_volume(volume)
                    return
            except mafic.PlayerNotConnected:
                continue

        logger.warning(
            "Failed to apply volume %s for guild %s (player not connected)",
            volume,
            player.guild.id,
        )

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave voice channel and clean local music state.

        This must work even when the Lavalink player is already stale/dead.
        `connection.get_player()` is intentionally stricter than `guild.voice_client`,
        so do not use it as the only source of truth for whether the bot is in voice.
        """
        raw_voice_client = guild.voice_client
        player = self.connection.get_player(guild.id)

        await self.ui.controller.destroy_for_guild(
            guild.id, ControllerDestroyReason.VOICE_DISCONNECT
        )
        await self.end_session(guild.id)
        self.state.cancel_timer(guild.id)

        if player:
            player.clear_queue()

        if raw_voice_client is None:
            return MusicResult(MusicResultStatus.FAILURE, "Not connected")

        await self.connection.disconnect(guild, force=True)

        if isinstance(raw_voice_client, mafic.Player) and (
            not isinstance(raw_voice_client, MusicPlayer)
            or not self.connection.is_player_usable(raw_voice_client)
            or self.connection.is_known_unavailable()
        ):
            return MusicResult(
                MusicResultStatus.FAILURE,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
            )

        return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")

    async def play(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        query: str,
        requester_id: int,
        text_channel_id: int | None = None,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        connection_result = await self.join(guild, voice_channel)
        check_result, _old_channel = connection_result
        if check_result.status is not MusicResultStatus.SUCCESS:
            return self._connection_failure_result(connection_result)

        player = self.connection.get_player(guild.id)
        if player is None:
            return self._lost_player_result()

        self._record_interaction_if_possible(guild.id, requester_id, text_channel_id)
        try:
            return await self._load_and_enqueue(
                player,
                query,
                requester_id,
                text_channel_id,
                connection_result,
            )
        except EXPECTED_PLAY_ERRORS as exc:
            return await self._handle_play_expected_failure(player, query, exc)
        except Exception as exc:
            return self._handle_play_unexpected_failure(exc)

    def _connection_failure_result(
        self, connection_result: VoiceJoinResult
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        return MusicResult(
            connection_result[0].status,
            "Connection failed",
            data=connection_result,
        )

    def _lost_player_result(self) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        return MusicResult(
            MusicResultStatus.ERROR,
            "Плеер потерял соединение. Попробуй запустить трек ещё раз.",
        )

    def _record_interaction_if_possible(
        self, guild_id: int, requester_id: int | None, text_channel_id: int | None
    ) -> None:
        if text_channel_id is not None and requester_id is not None:
            self.state.get_or_create_session(guild_id).record_interaction(
                text_channel_id, requester_id
            )

    async def _load_and_enqueue(
        self,
        player: MusicPlayer,
        query: str,
        requester_id: int,
        text_channel_id: int | None,
        connection_result: VoiceJoinResult,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        result = await player.fetch_tracks(query)
        if not result:
            return MusicResult(MusicResultStatus.FAILURE, "Nothing found")
        if isinstance(result, mafic.Playlist):
            return await self._enqueue_playlist(
                player, result, requester_id, text_channel_id, connection_result
            )
        return await self._enqueue_track(
            player, result[0], requester_id, text_channel_id, connection_result
        )

    async def _enqueue_playlist(
        self,
        player: MusicPlayer,
        playlist: mafic.Playlist,
        requester_id: int,
        text_channel_id: int | None,
        connection_result: VoiceJoinResult,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        for track in playlist.tracks:
            player.set_requester(track, requester_id, text_channel_id)
        player.queue.extend(playlist.tracks)
        await self._advance_if_idle(player)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Playlist added",
            data={
                "type": "playlist",
                "playlist": playlist,
                "connection": connection_result,
            },
        )

    async def _enqueue_track(
        self,
        player: MusicPlayer,
        track: mafic.Track,
        requester_id: int,
        text_channel_id: int | None,
        connection_result: VoiceJoinResult,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        player.set_requester(track, requester_id, text_channel_id)
        player.queue.append(track)
        is_playing_before = player.current is not None
        await self._advance_if_idle(player)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Track processed",
            data={
                "type": "track",
                "track": track,
                "playing": is_playing_before,
                "connection": connection_result,
            },
        )

    async def _advance_if_idle(self, player: MusicPlayer) -> None:
        if player.current is None:
            await player.advance()

    async def _handle_play_expected_failure(
        self, player: MusicPlayer, query: str, exc: Exception
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        if isinstance(exc, EXPECTED_LAVALINK_IO_ERRORS):
            await self._handle_player_io_failure(player, exc)
        logger.warning("Expected play failure for query '%s': %s", query, exc)
        safe_error = classify_music_exception(exc)
        status = (
            MusicResultStatus.FAILURE
            if isinstance(exc, mafic.TrackLoadException)
            else MusicResultStatus.ERROR
        )
        return MusicResult(status, safe_error.message)

    def _handle_play_unexpected_failure(
        self, exc: Exception
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        logger.exception("Error in play")
        safe_error = classify_music_exception(exc)
        return MusicResult(MusicResultStatus.ERROR, safe_error.message)

    async def stop(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return self._missing_player_result(guild_id, context="stop")

        try:
            player.clear_queue()
            await player.stop()
            await self.ui.controller.destroy_for_guild(
                guild_id, ControllerDestroyReason.MANUAL_STOP
            )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(MusicResultStatus.SUCCESS, "Stopped")

    async def skip(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[SkipTrackData]:
        if not (player := self.connection.get_player(guild_id)):
            return self._missing_player_result(guild_id, context="skip")

        current = player.current
        up_next = player.queue.next

        try:
            await player.skip()
            if current:
                await self.ui.controller.destroy_for_guild(
                    guild_id,
                    ControllerDestroyReason.SKIP,
                    expected_track_id=TrackId.from_track(current),
                )
            await player.resume()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Skipped",
            data={"before": current, "after": up_next},
        )

    async def pause(self, guild_id: int) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return self._missing_player_result(guild_id, context="pause")
        try:
            await player.pause()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)
        return MusicResult(MusicResultStatus.SUCCESS, "Paused")

    async def resume(self, guild_id: int) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return self._missing_player_result(guild_id, context="resume")
        try:
            await player.resume()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)
        return MusicResult(MusicResultStatus.SUCCESS, "Resumed")

    async def shuffle(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        if not (player := self.connection.get_player(guild_id)):
            return self._missing_player_result(guild_id, context="shuffle")
        try:
            player.queue.shuffle()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")

    async def rotate(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        player = self.connection.get_player(guild_id)
        if not player:
            return self._missing_player_result(guild_id, context="rotate")
        if not player.current:
            return MusicResult(MusicResultStatus.FAILURE, "Nothing playing")

        current = player.current
        try:
            player.queue.append(current)
            await player.skip()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

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
            except EXPECTED_LAVALINK_IO_ERRORS as exc:
                return await self._handle_player_io_failure(player, exc)
            except Exception as exc:
                logger.warning("Failed to apply volume: %s", exc)
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
            return self._missing_player_result(guild_id, context="set_repeat")

        try:
            previous = player.repeat.mode
            if mode is None:
                player.repeat.toggle()
            else:
                player.repeat.mode = mode
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Repeat updated",
            data={"mode": player.repeat.mode, "previous": previous},
        )

    async def get_queue(self, guild_id: int) -> MusicResult[QueueSnapshot]:
        player = self.connection.get_player(guild_id)
        if not player:
            return self._missing_player_result(guild_id, context="get_queue")
        if not player.queue and not player.current:
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

    def _missing_player_result(
        self, guild_id: int | None = None, *, context: str | None = None
    ) -> MusicResult[T]:
        if self.connection.is_known_unavailable():
            return MusicResult[T](
                MusicResultStatus.FAILURE,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
            )
        return player_fail_result(guild_id, context=context)

    async def _handle_player_io_failure[T](
        self, player: MusicPlayer, exc: Exception
    ) -> MusicResult[T]:
        logger.warning("Lavalink player IO failure: %s", type(exc).__name__)
        node = self.connection.get_player_node(player)
        if node is not None:
            await self.connection.mark_node_unavailable(node)
        else:
            await self.connection.mark_node_unavailable()
        await self.connection.detach_stale_voice_client(player.guild, player)
        return MusicResult(
            MusicResultStatus.FAILURE,
            MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
        )

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
        dispatch_music_session_end(self.bot, guild_id, session)

    async def cleanup(self) -> None:
        """Cleanup on shutdown."""
        for guild in self.bot.guilds:
            if guild.voice_client:
                await self.connection.disconnect(guild, force=True)
        self.events.cleanup()
        await self.connection.cleanup()
        self._initialized = False
        logger.info("CoreMusicService cleaned up.")
