from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
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
from repositories.volume_repository import VolumeRepository

from .models import ControllerDestroyReason, PlayerStateSnapshot, VoiceCheckResult
from .player import MusicPlayer

logger = logging.getLogger(__name__)

RESTORE_CONFIRM_DELAY_SECONDS = 1.5
RESTORE_SEEK_CONFIRM_DELAY_SECONDS = 1.0
RESTORE_SEEK_THRESHOLD_MS = 3_000


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
        *,
        guild_id: int,
        context: str,
    ) -> bool:
        """Confirm that Lavalink did not immediately drop the restored track."""
        await asyncio.sleep(RESTORE_CONFIRM_DELAY_SECONDS)

        if not self.connection.is_player_usable(player):
            logger.warning(
                "Restore failed for guild %s during %s: player became stale",
                guild_id,
                context,
            )
            await self.connection.detach_stale_voice_client(player.guild, player)
            return False

        if not player.current:
            logger.warning(
                "Restore failed for guild %s during %s: track did not stay active",
                guild_id,
                context,
            )
            await self.ui.controller.destroy_for_guild(
                guild_id,
                ControllerDestroyReason.TRACK_EXCEPTION,
            )
            return False

        return True

    async def _seek_after_warm_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        track: mafic.Track,
        position: int,
        volume: int,
        pause: bool,
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
            if player.current:
                with contextlib.suppress(*EXPECTED_LAVALINK_IO_ERRORS):
                    await player.set_volume(volume)
                if pause:
                    with contextlib.suppress(*EXPECTED_LAVALINK_IO_ERRORS):
                        await player.pause()
                return True
            return False

        await asyncio.sleep(RESTORE_SEEK_CONFIRM_DELAY_SECONDS)

        if player.current:
            try:
                await player.set_volume(volume)
                if pause:
                    await player.pause()
            except EXPECTED_LAVALINK_IO_ERRORS as exc:
                logger.warning(
                    "Restore post-seek state failed with %s",
                    type(exc).__name__,
                )
                return False
            return True

        logger.warning(
            "Restore seek to %sms killed playback; falling back to start",
            position,
        )

        try:
            await player.play(
                track,
                start_time=0,
                volume=volume,
                pause=pause,
            )
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            logger.warning(
                "Restore fallback playback from start failed with %s",
                type(exc).__name__,
            )
            await self.connection.detach_stale_voice_client(guild, player)
            return False

        return await self._confirm_restored_track_active(
            player,
            guild_id=player.guild.id,
            context="fallback-start-after-seek-failure",
        )

    async def _play_and_confirm_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        track: mafic.Track,
        start_time: int,
        volume: int,
        pause: bool,
    ) -> bool:
        """Play a restored track and confirm it survives early Lavalink failures."""
        try:
            await player.play(
                track,
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
            guild_id=player.guild.id,
            context="direct-restore",
        )

    async def _play_with_warm_seek_restore(
        self,
        *,
        guild: discord.Guild,
        player: MusicPlayer,
        track: mafic.Track,
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
            await player.play(
                track,
                start_time=0,
                volume=0,
                pause=False,
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
            guild_id=player.guild.id,
            context="warm-start",
        )
        if not active:
            return False

        return await self._seek_after_warm_restore(
            guild=guild,
            player=player,
            track=track,
            position=position,
            volume=volume,
            pause=pause,
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

        req_map = player._requester_map.copy()  # pyright: ignore[reportPrivateUsage]
        volume = await self.volume_repo.get_volume(guild_id=player.guild.id)

        return PlayerStateSnapshot(
            guild_id=player.guild.id,
            voice_channel_id=voice_channel_id,
            text_channel_id=text_channel_id,
            current_track=player.current,
            position=player.position or 0,
            is_paused=player.paused,
            volume=volume,
            queue=list(player.queue._queue),  # pyright: ignore[reportPrivateUsage]
            repeat_mode=player.repeat.mode,
            filters=None,
            requester_map=req_map,
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
        guild = self.bot.get_guild(snapshot.guild_id)
        if not guild:
            logger.warning("Cannot restore: guild %s not found", snapshot.guild_id)
            return False

        vc_channel = guild.get_channel(snapshot.voice_channel_id)
        if not vc_channel:
            logger.error(
                "Cannot restore: Voice channel %s not found",
                snapshot.voice_channel_id,
            )
            return False

        if isinstance(vc_channel, (ForumChannel, TextChannel, CategoryChannel)):
            raise ValueError("Invalid channel type for restoration")

        if not isinstance(vc_channel, (VoiceChannel, StageChannel)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("Invalid voice channel type for restoration")

        result, _old_channel = await self.connection.join(guild, vc_channel)
        playable_results = {
            VoiceCheckResult.SUCCESS,
            VoiceCheckResult.ALREADY_CONNECTED,
            VoiceCheckResult.MOVED_CHANNELS,
        }
        if result not in playable_results:
            logger.warning(
                "Cannot restore session for guild %s: voice join failed with %s",
                snapshot.guild_id,
                result,
            )
            return False

        player = self.connection.get_player(snapshot.guild_id)
        if not player:
            logger.warning(
                "Cannot restore session for guild %s: player failed to reconnect",
                snapshot.guild_id,
            )
            return False

        player.queue._queue.clear()  # pyright: ignore[reportPrivateUsage]
        player.queue._queue.extend(snapshot.queue)  # pyright: ignore[reportPrivateUsage]
        player.repeat.mode = snapshot.repeat_mode
        player._requester_map = snapshot.requester_map  # pyright: ignore[reportPrivateUsage]

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

        logger.debug("New player: %s", player)
        logger.debug(
            "Queue len: %s, Repeat mode: %s",
            len(player.queue),
            player.repeat.mode,
        )

        if snapshot.current_track:
            restored_track = await self._resolve_fresh_track_for_restore(
                player,
                snapshot.current_track,
            )

            restore_position = max(0, snapshot.position)

            if self._should_restore_with_warm_seek(restored_track, restore_position):
                restored = await self._play_with_warm_seek_restore(
                    guild=guild,
                    player=player,
                    track=restored_track,
                    position=restore_position,
                    volume=snapshot.volume,
                    pause=snapshot.is_paused,
                )
            else:
                restored = await self._play_and_confirm_restore(
                    guild=guild,
                    player=player,
                    track=restored_track,
                    start_time=restore_position,
                    volume=snapshot.volume,
                    pause=snapshot.is_paused,
                )

            if not restored:
                return False

            await self.ui.spawn_controller(player, restored_track)

        if session := snapshot.session:
            self.state.sessions.setdefault(snapshot.guild_id, session)

        self.snapshots.pop(snapshot.guild_id, None)
        return True
