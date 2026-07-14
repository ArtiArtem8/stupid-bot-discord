from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import override

import discord
import mafic
from discord import (
    CategoryChannel,
    ForumChannel,
    StageChannel,
    TextChannel,
    VoiceChannel,
)
from discord.abc import Connectable
from discord.ext import commands

from api.music.errors import EXPECTED_LAVALINK_IO_ERRORS
from api.music.protocols import HealerProtocol
from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from api.music.session_events import dispatch_music_session_end
from repositories.volume_repository import VolumeRepository

from .models import (
    ControllerDestroyReason,
    MusicResultStatus,
    PlaybackAttempt,
    PlayerStateSnapshot,
    QueueEntry,
)
from .player import MusicPlayer, tracks_match

logger = logging.getLogger(__name__)

RESTORE_CONFIRM_DELAY_SECONDS = 1.5
RESTORE_SEEK_CONFIRM_DELAY_SECONDS = 1.0
RESTORE_SEEK_THRESHOLD_MS = 3_000


@dataclass(frozen=True, slots=True)
class RestoreTarget:
    guild: discord.Guild
    channel: VoiceChannel | StageChannel


def _get_voice_channel_id(
    channel: VoiceChannel | StageChannel | Connectable | None,
) -> int | None:
    """Extract channel ID if it's a voice/stage channel."""
    return channel.id if isinstance(channel, (VoiceChannel, StageChannel)) else None


class SessionHealer(HealerProtocol):
    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
        volume_repository: VolumeRepository,
        ui_orchestrator: UIOrchestrator,
    ) -> None:
        self.bot = bot
        self.connection = connection_manager
        self.state = state_manager
        self.volume_repo = volume_repository
        self.ui = ui_orchestrator

        self.snapshots: dict[int, PlayerStateSnapshot] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _get_recoverable_player(self, guild_id: int) -> MusicPlayer | None:
        """Return a player suitable for snapshotting, even if it is no longer usable."""
        player = self.connection.get_player(guild_id)
        if player:
            return player

        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            return guild.voice_client

        return None

    def _is_youtube_track(self, track: mafic.Track) -> bool:
        """Return whether a track comes from YouTube."""
        source = getattr(track, "source", None) or getattr(track, "source_name", None)
        if isinstance(source, str) and source.lower() == "youtube":
            return True

        uri = getattr(track, "uri", None)
        if isinstance(uri, str):
            return "youtube.com" in uri or "youtu.be" in uri

        return False

    def _is_track_seekable(self, track: mafic.Track) -> bool:
        """Return whether a track can be sought."""
        seekable = getattr(track, "seekable", None)
        if isinstance(seekable, bool):
            return seekable

        is_seekable = getattr(track, "is_seekable", None)
        if isinstance(is_seekable, bool):
            return is_seekable

        return False

    def _should_restore_with_warm_seek(self, track: mafic.Track, position: int) -> bool:
        """Return whether restore should start from 0 and seek after startup."""
        if position <= RESTORE_SEEK_THRESHOLD_MS:
            return False

        # Main workaround: YouTube/MWEB can fail when starting a new track directly
        # from a non-zero position, while normal seek after playback start works.
        return self._is_youtube_track(track) and self._is_track_seekable(track)

    async def _confirm_restored_track_active(
        self,
        player: MusicPlayer,
        expected_attempt: PlaybackAttempt,
        *,
        guild_id: int,
        context: str,
    ) -> bool:
        """Confirm that Lavalink did not immediately drop the restored track."""
        await asyncio.sleep(RESTORE_CONFIRM_DELAY_SECONDS)

        if self._is_expected_restore_attempt_active(player, expected_attempt):
            return True

        return await self._fail_expected_restore_attempt(
            player,
            expected_attempt,
            guild_id=guild_id,
            context=context,
        )

    def _is_expected_restore_attempt_active(
        self,
        player: MusicPlayer,
        expected_attempt: PlaybackAttempt,
    ) -> bool:
        current = player.current
        return (
            player.current_attempt is expected_attempt
            and self.connection.is_player_usable(player)
            and current is not None
            and tracks_match(expected_attempt.entry.track, current)
        )

    async def _fail_expected_restore_attempt(
        self,
        player: MusicPlayer,
        expected_attempt: PlaybackAttempt,
        *,
        guild_id: int,
        context: str,
    ) -> bool:
        claimed = await player.invalidate_if_current_attempt(expected_attempt)
        if not claimed:
            logger.debug(
                "Restore attempt superseded in guild %s during %s",
                guild_id,
                context,
            )
            return False

        logger.warning(
            "Restore attempt failed in guild %s during %s",
            guild_id,
            context,
        )
        await self.connection.detach_stale_voice_client(player.guild, player)
        return False

    async def _restore_expected_attempt_state(
        self,
        player: MusicPlayer,
        expected_attempt: PlaybackAttempt,
        *,
        guild_id: int,
        volume: int,
        pause: bool,
        context: str,
    ) -> bool:
        if not self._is_expected_restore_attempt_active(player, expected_attempt):
            return await self._fail_expected_restore_attempt(
                player,
                expected_attempt,
                guild_id=guild_id,
                context=context,
            )

        try:
            await player.set_volume(volume)
            if pause:
                await player.pause()
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Restore volume/pause failed with %s during %s",
                type(exc).__name__,
                context,
            )
            return await self._fail_expected_restore_attempt(
                player,
                expected_attempt,
                guild_id=guild_id,
                context=context,
            )

        if self._is_expected_restore_attempt_active(player, expected_attempt):
            return True

        return await self._fail_expected_restore_attempt(
            player,
            expected_attempt,
            guild_id=guild_id,
            context=context,
        )

    async def _seek_after_warm_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        entry: QueueEntry,
        position: int,
        volume: int,
        pause: bool,
        expected_attempt: PlaybackAttempt,
    ) -> bool:
        """Seek after a successful warm restore.

        If the seek itself breaks playback, fall back to playing from the start
        instead of disconnecting from voice.
        """
        try:
            await player.seek(position)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Restore seek to %sms failed with %s; keeping playback from start",
                position,
                type(exc).__name__,
            )
            return await self._restore_expected_attempt_state(
                player,
                expected_attempt,
                guild_id=guild.id,
                volume=volume,
                pause=pause,
                context="state-after-warm-seek-error",
            )

        await asyncio.sleep(RESTORE_SEEK_CONFIRM_DELAY_SECONDS)

        if self._is_expected_restore_attempt_active(player, expected_attempt):
            return await self._restore_expected_attempt_state(
                player,
                expected_attempt,
                guild_id=guild.id,
                volume=volume,
                pause=pause,
                context="post-warm-seek-state",
            )

        if player.current_attempt is not expected_attempt:
            return await self._fail_expected_restore_attempt(
                player,
                expected_attempt,
                guild_id=guild.id,
                context="warm-seek-superseded",
            )

        logger.warning(
            "Restore seek to %sms killed playback; falling back to start",
            position,
        )

        try:
            fallback_attempt = await player.restore_playback(
                entry,
                start_time=0,
                volume=volume,
                pause=pause,
            )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Restore fallback playback from start failed with %s",
                type(exc).__name__,
            )
            return await self._fail_expected_restore_attempt(
                player,
                expected_attempt,
                guild_id=guild.id,
                context="fallback-start-after-seek-failure",
            )

        return await self._confirm_restored_track_active(
            player,
            fallback_attempt,
            guild_id=player.guild.id,
            context="fallback-start-after-seek-failure",
        )

    async def _play_and_confirm_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        entry: QueueEntry,
        start_time: int,
        volume: int,
        pause: bool,
    ) -> bool:
        """Play a restored track and confirm it survives early Lavalink failures."""
        try:
            attempt = await player.restore_playback(
                entry,
                start_time=start_time,
                volume=volume,
                pause=pause,
            )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Restore playback failed immediately with %s",
                type(exc).__name__,
            )
            await self.connection.detach_stale_voice_client(guild, player)
            return False

        return await self._confirm_restored_track_active(
            player,
            attempt,
            guild_id=player.guild.id,
            context="direct-restore",
        )

    async def _play_with_warm_seek_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        entry: QueueEntry,
        position: int,
        volume: int,
        pause: bool,
    ) -> bool:
        """Restore YouTube playback by starting at 0, then seeking after startup."""
        logger.warning(
            (
                "Restoring YouTube track from 0, then seeking to %sms "
                "to avoid youtube-source 403 on initial non-zero playback."
            ),
            position,
        )

        # Keep this silent during warmup so users do not hear the first second twice.
        try:
            attempt = await player.restore_playback(
                entry,
                start_time=0,
                volume=0,
                pause=pause,
            )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Warm restore playback failed immediately with %s",
                type(exc).__name__,
            )
            await self.connection.detach_stale_voice_client(guild, player)
            return False

        active = await self._confirm_restored_track_active(
            player,
            attempt,
            guild_id=player.guild.id,
            context="warm-start",
        )
        if not active:
            return False

        return await self._seek_after_warm_restore(
            guild=guild,
            player=player,
            entry=entry,
            position=position,
            volume=volume,
            pause=pause,
            expected_attempt=attempt,
        )

    def _restore_start_time(self, track: mafic.Track, position: int) -> int:
        """Choose safe start time for restored playback.

        YouTube playback through youtube-source/MWEB can fail with 403 when restored
        from a non-zero position. Prefer a reliable restart over a broken resume.
        """
        safe_position = max(0, position)

        if safe_position <= 0:
            return 0

        if self._is_youtube_track(track):
            logger.warning(
                (
                    "Restoring YouTube track from start instead of position %sms "
                    "to avoid youtube-source 403 on reconnect."
                ),
                safe_position,
            )
            return 0

        return safe_position

    async def _resolve_fresh_track_for_restore(
        self,
        player: MusicPlayer,
        track: mafic.Track,
    ) -> mafic.Track:
        """Resolve a fresh track object before restoring playback.

        Reusing an old YouTube encoded track after reconnect can fail with stale
        stream metadata. Prefer resolving by URI and fall back to the snapshot track.
        """
        if not track.uri:
            return track

        try:
            result = await player.fetch_tracks(track.uri)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Failed to refresh restore track %s with %s; using snapshot track",
                track.uri,
                type(exc).__name__,
            )
            return track
        except Exception:
            logger.debug("Unexpected restore track refresh failure", exc_info=True)
            return track

        if isinstance(result, mafic.Playlist):
            if result.tracks:
                return result.tracks[0]
            return track

        if result:
            return result[0]

        return track

    @override
    async def capture_and_heal(self, guild_id: int) -> bool:
        """Main entry point to attempt a session recovery."""
        async with self._locks[guild_id]:
            logger.info("Attempting to heal session for guild %s", guild_id)

            player = self._get_recoverable_player(guild_id)
            if not player:
                logger.warning("Cannot heal: No player found for %s", guild_id)
                return False

            try:
                snapshot = await self._create_snapshot(player)
                self.snapshots[guild_id] = snapshot

                await self._hard_disconnect(guild_id, player)

                await asyncio.sleep(2.0)

                restored = await self._restore_session(snapshot)
                if not restored:
                    logger.warning(
                        "Session heal did not restore playback for guild %s",
                        guild_id,
                    )
                    return False

                logger.info("Session healed successfully for guild %s", guild_id)
                return True
            except Exception:
                logger.exception("Failed to heal session for %s", guild_id)
                self.snapshots.pop(guild_id, None)
                return False

    @override
    async def cleanup_after_disconnect(
        self, guild_id: int, is_healing: bool = False
    ) -> None:
        """Cleanup after disconnect. During healing, preserve recoverable state."""
        await self.ui.controller.destroy_for_guild(
            guild_id, ControllerDestroyReason.VOICE_DISCONNECT
        )

        if is_healing:
            self.state.cancel_timer(guild_id)
            return

        session = self.state.end_session(guild_id)
        dispatch_music_session_end(self.bot, guild_id, session)

        self.state.cancel_timer(guild_id)

        player = self.connection.get_player(guild_id)
        if player:
            player.clear_queue()

    async def _create_snapshot(self, player: MusicPlayer) -> PlayerStateSnapshot:
        """Extracts deep state from the player."""
        voice_channel_id = _get_voice_channel_id(player.channel)
        if not voice_channel_id and (vc_client := player.guild.voice_client):
            voice_channel_id = _get_voice_channel_id(vc_client.channel)

        if not voice_channel_id:
            raise ValueError("Cannot snapshot: Player has no active voice channel")

        session = self.state.get_session(player.guild.id)
        text_channel_id = None
        if session:
            text_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )

        volume = await self.volume_repo.get_volume(guild_id=player.guild.id)

        return PlayerStateSnapshot(
            guild_id=player.guild.id,
            voice_channel_id=voice_channel_id,
            text_channel_id=text_channel_id,
            current_entry=player.current_entry,
            position=player.position or 0,
            is_paused=player.paused,
            volume=volume,
            queue=list(player.queue_snapshot()),
            repeat_mode=player.repeat.mode,
            filters=None,
            session=session,
        )

    async def _hard_disconnect(self, guild_id: int, player: MusicPlayer) -> None:
        """Forcefully disconnect via ConnectionManager stale voice state is cleaned."""
        try:
            await self.connection.disconnect(player.guild, force=True)
        except Exception:
            logger.exception("Failed to hard disconnect for guild %s", guild_id)

    async def _restore_session(self, snapshot: PlayerStateSnapshot) -> bool:
        """Rebuild the player from the snapshot using ConnectionManager safeguards."""
        target = self._resolve_restore_target(snapshot)
        if target is None:
            return False

        if not await self._join_restore_voice(target, snapshot.guild_id):
            return False

        player = self._get_restored_player(snapshot.guild_id)
        if player is None:
            return False

        self.state.clear_track_start_times(snapshot.guild_id)
        if not await self._restore_player_runtime_state(player, snapshot, target.guild):
            return False

        return await self._restore_current_track(player, snapshot, target.guild)

    def _resolve_restore_target(
        self, snapshot: PlayerStateSnapshot
    ) -> RestoreTarget | None:
        guild = self.bot.get_guild(snapshot.guild_id)
        if not guild:
            logger.warning("Cannot restore: guild %s not found", snapshot.guild_id)
            return None

        channel = self._get_restore_voice_channel(guild, snapshot)
        if channel is None:
            logger.error(
                "Cannot restore: Voice channel %s not found",
                snapshot.voice_channel_id,
            )
            return None
        return RestoreTarget(guild, channel)

    def _get_restore_voice_channel(
        self, guild: discord.Guild, snapshot: PlayerStateSnapshot
    ) -> VoiceChannel | StageChannel | None:
        channel = guild.get_channel(snapshot.voice_channel_id)
        if channel is None:
            return None
        if isinstance(channel, (ForumChannel, TextChannel, CategoryChannel)):
            raise ValueError("Invalid channel type for restoration")
        if not isinstance(channel, (VoiceChannel, StageChannel)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("Invalid voice channel type for restoration")
        return channel

    async def _join_restore_voice(self, target: RestoreTarget, guild_id: int) -> bool:
        result, _old_channel = await self.connection.join(target.guild, target.channel)
        if result.status is not MusicResultStatus.SUCCESS:
            logger.warning(
                "Cannot restore session for guild %s: voice join failed with %s",
                guild_id,
                result,
            )
            return False
        return True

    def _get_restored_player(self, guild_id: int) -> MusicPlayer | None:
        player = self.connection.get_player(guild_id)
        if not player:
            logger.warning(
                "Cannot restore session for guild %s: player failed to reconnect",
                guild_id,
            )
        return player

    def _restore_player_entries(
        self, player: MusicPlayer, snapshot: PlayerStateSnapshot
    ) -> None:
        player.restore_entries(snapshot.current_entry, snapshot.queue)
        player.repeat.mode = snapshot.repeat_mode

    async def _restore_player_runtime_state(
        self,
        player: MusicPlayer,
        snapshot: PlayerStateSnapshot,
        guild: discord.Guild,
    ) -> bool:
        self._restore_player_entries(player, snapshot)
        if not await self._restore_player_volume(player, snapshot, guild):
            return False

        logger.debug("New player: %s", player)
        logger.debug(
            "Queue len: %s, Repeat mode: %s",
            len(player.queue),
            player.repeat.mode,
        )
        return True

    async def _restore_player_volume(
        self,
        player: MusicPlayer,
        snapshot: PlayerStateSnapshot,
        guild: discord.Guild,
    ) -> bool:
        try:
            await player.set_volume(snapshot.volume)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Cannot restore session for guild %s: volume restore failed with %s",
                snapshot.guild_id,
                type(exc).__name__,
            )
            await self.connection.detach_stale_voice_client(guild, player)
            return False
        return True

    async def _restore_current_track(
        self,
        player: MusicPlayer,
        snapshot: PlayerStateSnapshot,
        guild: discord.Guild,
    ) -> bool:
        if snapshot.current_entry is not None:
            restored_track = await self._resolve_fresh_track_for_restore(
                player, snapshot.current_entry.track
            )
            restored_entry = replace(snapshot.current_entry, track=restored_track)

            restore_position = max(0, snapshot.position)

            if self._should_restore_with_warm_seek(restored_track, restore_position):
                restored = await self._play_with_warm_seek_restore(
                    guild=guild,
                    player=player,
                    entry=restored_entry,
                    position=restore_position,
                    volume=snapshot.volume,
                    pause=snapshot.is_paused,
                )
            else:
                restored = await self._play_and_confirm_restore(
                    guild=guild,
                    player=player,
                    entry=restored_entry,
                    start_time=restore_position,
                    volume=snapshot.volume,
                    pause=snapshot.is_paused,
                )

            if not restored:
                return False

            attempt = player.current_attempt
            if attempt is not None:
                self.state.record_track_start(snapshot.guild_id, attempt)
                await self.ui.spawn_controller(player, attempt)

        if snapshot.session is not None:
            session = snapshot.session
            self.state.sessions.setdefault(snapshot.guild_id, session)

        self.snapshots.pop(snapshot.guild_id, None)
        return True
