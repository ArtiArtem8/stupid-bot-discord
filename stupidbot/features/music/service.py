"""Music orchestration over one explicit component graph."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Never, TypeVar

import discord
import mafic
from discord.ext import commands

from stupidbot.features.music.contracts import ControllerDestroyReason
from stupidbot.features.music.discord.controller_delivery import UIOrchestrator
from stupidbot.features.music.errors import (
    EXPECTED_LAVALINK_IO_ERRORS,
    NodeNotConnectedError,
    classify_music_exception,
    compact_external_log_text,
)
from stupidbot.features.music.playback.models import (
    EnqueueOutcome,
    PlaybackAttempt,
    PlayPlacement,
    QueueEntry,
    QueuePlacement,
    QueueSnapshot,
    RepeatMode,
    Track,
    TrackRequester,
)
from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.results import (
    MUSIC_RECOVERING_MESSAGE,
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    MusicErrorCode,
    MusicResult,
    MusicResultReason,
    MusicResultStatus,
    PlayResponseData,
    RepeatModeData,
    RotateTrackData,
    SkipTrackData,
    VoiceCheckResult,
    VoiceJoinResult,
)
from stupidbot.features.music.runtime.connection import ConnectionManager
from stupidbot.features.music.runtime.playback_events import PlaybackEventHandlers
from stupidbot.features.music.runtime.session import dispatch_music_session_end
from stupidbot.features.music.runtime.state import EmptyTimerInfo, StateManager
from stupidbot.features.music.runtime.voice_lifecycle import VoiceLifecycleHandlers
from stupidbot.features.music.volume import VolumeSettings

logger = logging.getLogger(__name__)

T = TypeVar("T")
EXPECTED_PLAY_ERRORS = (
    mafic.TrackLoadException,
    mafic.PlayerNotConnected,
    mafic.PlayerException,
    NodeNotConnectedError,
)


class CoreMusicService:
    """Coordinate music operations while components retain ownership of their state."""

    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
        volume_settings: VolumeSettings,
        playback_events: PlaybackEventHandlers,
        voice_lifecycle: VoiceLifecycleHandlers,
        ui_orchestrator: UIOrchestrator,
    ) -> None:
        self.bot = bot
        self.connection = connection_manager
        self.state = state_manager
        self.volume_settings = volume_settings
        self.volume_repo = volume_settings.repository
        self.playback_events = playback_events
        self.voice_lifecycle = voice_lifecycle
        self.ui = ui_orchestrator
        self._initialized = False
        self._closing = False

    async def initialize(self) -> None:
        """Register listeners and start the background node connection."""
        if self._closing:
            return
        if self._initialized:
            logger.debug("CoreMusicService already initialized.")
            return

        self.playback_events.setup()
        self.voice_lifecycle.setup()
        self._initialized = True
        self.connection.start_lazy_connect()
        logger.info("CoreMusicService initialized.")

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Get the music player for a guild."""
        return self.connection.get_player(guild_id)

    def _is_recovering(self, guild_id: int) -> bool:
        return self.voice_lifecycle.is_healing(
            guild_id
        ) or self.connection.is_recovering(guild_id)

    async def heal(self, guild_id: int) -> bool:
        """Attempt to heal the session for the given guild."""
        return await self.voice_lifecycle.heal(guild_id)

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel and apply the guild's saved volume."""
        if self._is_recovering(guild.id):
            return VoiceCheckResult.RECOVERING, None
        result, old_channel = await self.connection.join(guild, channel)

        if result.status == MusicResultStatus.SUCCESS:
            player = self.connection.get_player(
                guild.id,
                failure_context="successful_join_missing_player",
            )
            if player is None:
                return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
            try:
                async with self.volume_settings.operation(guild.id) as vol:
                    if self.connection.get_player(guild.id) is not player:
                        return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
                    await player.set_volume(vol)
            except EXPECTED_LAVALINK_IO_ERRORS as exc:
                await self._handle_player_io_failure(player, exc)
                return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
            if self.connection.get_player(guild.id) is not player:
                return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None

        return result, old_channel

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave the voice channel even when its Lavalink player is stale.

        Use ``guild.voice_client`` because ``connection.get_player()`` hides stale
        players that still need to be disconnected.
        """
        async with self.voice_lifecycle.leaving(guild.id):
            await self.connection.cancel_recovery(guild.id)
            raw_voice_client = guild.voice_client

            await self.ui.controller.destroy_for_guild(
                guild.id, ControllerDestroyReason.VOICE_DISCONNECT
            )
            await self.end_session(guild.id)
            self.state.cancel_timer(guild.id)

            if isinstance(raw_voice_client, MusicPlayer):
                raw_voice_client.clear_queue()

            if raw_voice_client is None:
                return MusicResult(
                    MusicResultStatus.FAILURE,
                    "Not connected",
                    reason=MusicResultReason.NOT_CONNECTED,
                )

            disconnected = await self.connection.disconnect(guild, force=True)

            if disconnected:
                return MusicResult(
                    MusicResultStatus.SUCCESS,
                    "Disconnected",
                    reason=MusicResultReason.DISCONNECTED,
                )
            return MusicResult(
                MusicResultStatus.ERROR,
                "Не удалось отключиться от голосового канала.",
                reason=MusicResultReason.DISCONNECT_FAILED,
            )

    async def play(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        query: str,
        requester_id: int,
        text_channel_id: int | None = None,
        *,
        placement: QueuePlacement = "end",
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        connection_result = await self.join(guild, voice_channel)
        check_result, _old_channel = connection_result
        if check_result.status is not MusicResultStatus.SUCCESS:
            return self._connection_failure_result(connection_result)

        player = self.connection.get_player(
            guild.id, failure_context="play_after_join_validation"
        )
        if player is None:
            return MusicResult(
                MusicResultStatus.ERROR,
                "Плеер потерял соединение. Попробуй запустить трек ещё раз.",
                reason=MusicResultReason.PLAYER_LOST,
            )

        self._record_interaction_if_possible(guild.id, requester_id, text_channel_id)
        try:
            return await self._load_and_enqueue(
                player,
                query,
                requester_id,
                text_channel_id,
                connection_result,
                placement=placement,
            )
        except EXPECTED_PLAY_ERRORS as exc:
            return await self._handle_play_expected_failure(player, query, exc)

    def _connection_failure_result(
        self, connection_result: VoiceJoinResult
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        return MusicResult(
            connection_result[0].status,
            "Connection failed",
            data=connection_result,
            reason=MusicResultReason.CONNECTION_FAILED,
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
        *,
        placement: QueuePlacement,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        result = await player.fetch_tracks(query)
        if self._is_recovering(
            player.guild.id
        ) or not self.connection.is_current_player(player):
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Плеер изменился во время поиска. Попробуй запустить трек ещё раз.",
                reason=MusicResultReason.PLAYER_CHANGED,
            )
        if not result:
            query_text = compact_external_log_text(query)
            logger.debug(
                "Track load returned no results guild=%s query=%r",
                player.guild.id,
                query_text,
            )
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Nothing found",
                reason=MusicResultReason.NOTHING_FOUND,
            )
        if isinstance(result, mafic.Playlist):
            return await self._enqueue_playlist(
                player,
                result,
                requester_id,
                text_channel_id,
                connection_result,
                placement=placement,
            )
        return await self._enqueue_track(
            player,
            result[0],
            requester_id,
            text_channel_id,
            connection_result,
            placement=placement,
        )

    async def _enqueue_playlist(
        self,
        player: MusicPlayer,
        playlist: mafic.Playlist,
        requester_id: int,
        text_channel_id: int | None,
        connection_result: VoiceJoinResult,
        *,
        placement: QueuePlacement,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        if not playlist.tracks:
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Nothing found",
                reason=MusicResultReason.NOTHING_FOUND,
            )

        outcome = await self._enqueue_tracks_for_play(
            player,
            playlist.tracks,
            requester_id,
            text_channel_id,
            placement=placement,
        )
        undo_entries = outcome.entries if outcome.has_waiting_entries else ()
        response_placement: PlayPlacement = (
            "now" if outcome.started_from_enqueue else placement
        )
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Playlist added",
            data={
                "type": "playlist",
                "playlist": playlist,
                "undo_entries": undo_entries,
                "placement": response_placement,
                "connection": connection_result,
            },
            reason=MusicResultReason.PLAYLIST_ADDED,
        )

    async def _enqueue_track(
        self,
        player: MusicPlayer,
        track: mafic.Track,
        requester_id: int,
        text_channel_id: int | None,
        connection_result: VoiceJoinResult,
        *,
        placement: QueuePlacement,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        outcome = await self._enqueue_tracks_for_play(
            player,
            (track,),
            requester_id,
            text_channel_id,
            placement=placement,
        )
        undo_entries = outcome.entries if outcome.has_waiting_entries else ()
        response_placement: PlayPlacement = (
            "now" if outcome.started_from_enqueue else placement
        )
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Track processed",
            data={
                "type": "track",
                "track": track,
                "undo_entries": undo_entries,
                "placement": response_placement,
                "connection": connection_result,
            },
            reason=MusicResultReason.TRACK_PROCESSED,
        )

    async def _enqueue_tracks_for_play(
        self,
        player: MusicPlayer,
        tracks: Sequence[Track],
        requester_id: int,
        text_channel_id: int | None,
        *,
        placement: QueuePlacement,
    ) -> EnqueueOutcome:
        requester = TrackRequester(requester_id, text_channel_id)
        outcome = await player.enqueue_tracks(tracks, requester, placement=placement)
        if not self.connection.is_current_player(player):
            raise mafic.PlayerNotConnected
        return outcome

    async def remove_queued_entries(
        self,
        guild_id: int,
        expected: Sequence[QueueEntry],
        requester_id: int,
    ) -> MusicResult[tuple[QueueEntry, ...]]:
        """Remove exact waiting entries from the active guild player."""
        player = self._operation_player(guild_id, context="remove queued entries")
        if isinstance(player, MusicResult):
            return player
        removed = await player.remove_queued_entries(
            expected,
            requester_id=requester_id,
        )
        if not removed:
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Queued entries are stale",
                reason=MusicResultReason.STALE_QUEUE_ENTRIES,
            )
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Removed from queue",
            data=removed,
            reason=MusicResultReason.REMOVED_FROM_QUEUE,
        )

    async def _handle_play_expected_failure(
        self, player: MusicPlayer, query: str, exc: Exception
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        query_text = compact_external_log_text(query)
        if isinstance(exc, mafic.TrackLoadException):
            message = compact_external_log_text(exc.message)
            cause = compact_external_log_text(exc.cause)
            message_format = (
                "Track load failure guild=%s query=%r severity=%s message=%r cause=%r"
            )
            logger.warning(
                message_format,
                player.guild.id,
                query_text,
                exc.severity,
                message,
                cause,
            )
        elif isinstance(exc, EXPECTED_LAVALINK_IO_ERRORS):
            await self._handle_player_io_failure(player, exc)
        else:
            logger.warning(
                "Node unavailable during track load guild=%s query=%r error=%s",
                player.guild.id,
                query_text,
                type(exc).__name__,
            )
        safe_error = classify_music_exception(exc)
        status = (
            MusicResultStatus.FAILURE
            if isinstance(exc, mafic.TrackLoadException)
            else MusicResultStatus.ERROR
        )
        return MusicResult(status, safe_error.message, reason=safe_error.code)

    async def stop(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        player = self._operation_player(guild_id, context="stop")
        if isinstance(player, MusicResult):
            return player

        try:
            await self.ui.controller.destroy_for_guild(
                guild_id, ControllerDestroyReason.MANUAL_STOP
            )
            await player.stop_and_clear()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS, "Stopped", reason=MusicResultReason.STOPPED
        )

    async def skip(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
        *,
        expected: PlaybackAttempt | None = None,
    ) -> MusicResult[SkipTrackData]:
        player = self._operation_player(guild_id, context="skip")
        if isinstance(player, MusicResult):
            return player

        try:
            skipped_attempt, started_attempt = await player.skip(expected=expected)
            if skipped_attempt:
                await self.ui.controller.destroy_for_guild(
                    guild_id,
                    ControllerDestroyReason.SKIP,
                    expected_attempt_id=skipped_attempt.attempt_id,
                    expected_player=player,
                )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Skipped",
            data={
                "before": skipped_attempt.entry.track if skipped_attempt else None,
                "after": started_attempt.entry.track if started_attempt else None,
            },
            reason=MusicResultReason.SKIPPED,
        )

    async def pause(self, guild_id: int) -> MusicResult[None]:
        player = self._operation_player(guild_id, context="pause")
        if isinstance(player, MusicResult):
            return player
        try:
            await player.pause()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)
        return MusicResult(
            MusicResultStatus.SUCCESS, "Paused", reason=MusicResultReason.PAUSED
        )

    async def resume(self, guild_id: int) -> MusicResult[None]:
        player = self._operation_player(guild_id, context="resume")
        if isinstance(player, MusicResult):
            return player
        try:
            await player.resume()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)
        return MusicResult(
            MusicResultStatus.SUCCESS, "Resumed", reason=MusicResultReason.RESUMED
        )

    async def shuffle(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        player = self._operation_player(guild_id, context="shuffle")
        if isinstance(player, MusicResult):
            return player
        await player.shuffle_queue()

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS, "Shuffled", reason=MusicResultReason.SHUFFLED
        )

    async def rotate(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        player = self._operation_player(guild_id, context="rotate")
        if isinstance(player, MusicResult):
            return player

        try:
            moved_attempt, started_attempt = await player.rotate_current()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            return await self._handle_player_io_failure(player, exc)

        if not moved_attempt:
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Nothing playing",
                reason=MusicResultReason.NOTHING_PLAYING,
            )

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Rotated",
            data={
                "skipped": moved_attempt.entry.track,
                "next": started_attempt.entry.track if started_attempt else None,
            },
            reason=MusicResultReason.ROTATED,
        )

    async def set_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        if self._is_recovering(guild_id):
            return MusicResult(
                MusicResultStatus.FAILURE,
                MUSIC_RECOVERING_MESSAGE,
                reason=MusicResultReason.RECOVERING,
            )
        async with self.volume_settings.operation(guild_id, desired=volume) as current:
            player = self.connection.get_player(guild_id)
            if player:
                try:
                    await player.set_volume(current)
                except EXPECTED_LAVALINK_IO_ERRORS as exc:
                    return await self._handle_player_io_failure(player, exc)
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Volume set",
            data=volume,
            reason=MusicResultReason.VOLUME_SET,
        )

    async def get_volume(self, guild_id: int) -> int:
        return await self.volume_repo.get_volume(guild_id)

    async def set_repeat(
        self,
        guild_id: int,
        mode: RepeatMode | None = None,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RepeatModeData]:
        player = self._operation_player(guild_id, context="set_repeat")
        if isinstance(player, MusicResult):
            return player

        previous = player.repeat.mode
        if mode is None:
            player.repeat.toggle()
        else:
            player.repeat.mode = mode

        self._record_interaction_if_possible(guild_id, requester_id, text_channel_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Repeat updated",
            data={"mode": player.repeat.mode, "previous": previous},
            reason=MusicResultReason.REPEAT_UPDATED,
        )

    async def get_queue(self, guild_id: int) -> MusicResult[QueueSnapshot]:
        player = self.connection.get_player(guild_id)
        if not player:
            return self._missing_player_result(guild_id, context="get_queue")
        if not player.queue and not player.current_entry:
            return MusicResult(
                MusicResultStatus.FAILURE,
                "Queue empty",
                reason=MusicResultReason.QUEUE_EMPTY,
            )

        snapshot = QueueSnapshot(
            current=player.current_entry,
            queue=tuple(player.queue),
            repeat_mode=player.repeat.mode,
        )
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Retrieved",
            data=snapshot,
            reason=MusicResultReason.QUEUE_RETRIEVED,
        )

    async def get_queue_duration(self, guild_id: int) -> int:
        player = self.connection.get_player(guild_id)
        if not player:
            return 0
        total = player.queue.duration
        if player.current_entry:
            position = player.position or 0
            total += max(0, player.current_entry.track.length - position)
        return total

    def _operation_player(
        self, guild_id: int, *, context: str
    ) -> MusicPlayer | MusicResult[Never]:
        """Reject mutations during recovery before looking up an active player."""
        if self._is_recovering(guild_id):
            return MusicResult(
                MusicResultStatus.FAILURE,
                MUSIC_RECOVERING_MESSAGE,
                reason=MusicResultReason.RECOVERING,
            )
        player = self.connection.get_player(guild_id)
        if player is None:
            return self._missing_player_result(guild_id, context=context)
        return player

    def _missing_player_result(
        self, guild_id: int | None = None, *, context: str | None = None
    ) -> MusicResult[T]:
        if self.connection.is_known_unavailable():
            logger.info(
                "Node unavailable for music operation guild=%s context=%s",
                guild_id,
                context,
            )
            return MusicResult[T](
                MusicResultStatus.FAILURE,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
                reason=MusicErrorCode.MUSIC_NODE_UNAVAILABLE,
            )
        return player_fail_result(guild_id, context=context)

    async def _handle_player_io_failure[T](
        self, player: MusicPlayer, exc: Exception
    ) -> MusicResult[T]:
        await self.connection.invalidate_player(
            player,
            context="player_operation_io_failure",
            error=type(exc).__name__,
        )
        safe_error = classify_music_exception(exc)
        return MusicResult(
            MusicResultStatus.FAILURE, safe_error.message, reason=safe_error.code
        )

    async def check_auto_leave(self) -> None:
        """Check for guilds that have been empty for too long."""
        candidates = [
            (
                guild_id,
                self.state.empty_channel_timers.get(guild_id),
                self.connection.get_player(guild_id),
            )
            for guild_id in self.state.check_auto_leave()
        ]
        for guild_id, timer, player in candidates:
            if timer is None or player is None:
                continue
            try:
                await self._leave_expired(guild_id, timer, player)
            except Exception:
                logger.exception("Failed to auto-leave guild %s", guild_id)

    async def _leave_expired(
        self, guild_id: int, timer: EmptyTimerInfo, player: MusicPlayer
    ) -> None:
        channel = player.channel
        if (
            self.state.empty_channel_timers.get(guild_id) is not timer
            or not self.connection.is_current_player(player)
            or self._is_recovering(guild_id)
            or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel))
            or self.voice_lifecycle.empty_channel_reason(channel) is None
        ):
            return
        async with self.voice_lifecycle.leaving(guild_id):
            if not await self.connection.disconnect(player.guild, force=True):
                return
            if player.guild.voice_client is not None:
                return
            self.state.cancel_timer_if_current(guild_id, timer)
            await self.end_session(guild_id)
            await self.ui.controller.destroy_for_guild(
                guild_id,
                ControllerDestroyReason.VOICE_DISCONNECT,
                expected_player=player,
            )

    async def end_session(self, guild_id: int) -> None:
        """End the music session and dispatch the event."""
        session = self.state.end_session(guild_id)
        dispatch_music_session_end(self.bot, guild_id, session)

    async def cleanup(self) -> None:
        """Stop recovery producers before disconnecting and disposing voice I/O."""
        self._closing = True
        await self.playback_events.cleanup()
        await self.voice_lifecycle.cleanup()
        await self.connection.stop_connecting()
        try:
            for guild in self.bot.guilds:
                if guild.voice_client:
                    await self.leave(guild)
        finally:
            await self.connection.cleanup()
            self._initialized = False
        logger.info("CoreMusicService cleaned up.")


def player_fail_result(
    guild_id: int | None = None, *, context: str | None = None
) -> MusicResult[T]:
    """Create a standardized failure result when a music player is missing."""
    if guild_id is not None:
        suffix = f" ({context})" if context else ""
        logger.debug("No player for guild_id=%s%s", guild_id, suffix)
    return MusicResult(
        MusicResultStatus.FAILURE, "No player", reason=MusicResultReason.NO_PLAYER
    )
