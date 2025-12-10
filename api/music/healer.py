from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from discord import (
    CategoryChannel,
    ForumChannel,
    StageChannel,
    TextChannel,
    VoiceChannel,
)
from discord.abc import Connectable

from .models import PlayerStateSnapshot
from .player import music_player_factory

if TYPE_CHECKING:
    from .player import MusicPlayer
    from .service import MusicService

LOGGER = logging.getLogger(__name__)


def _get_voice_channel_id(
    channel: VoiceChannel | StageChannel | Connectable | None,
) -> int | None:
    """Extract channel ID if it's a voice/stage channel."""
    return channel.id if isinstance(channel, VoiceChannel | StageChannel) else None


class SessionHealer:
    def __init__(self, service: MusicService):
        self.service = service
        self.snapshots: dict[int, PlayerStateSnapshot] = {}
        self._locks = defaultdict(asyncio.Lock)

    async def capture_and_heal(self, guild_id: int):
        """Main entry point to attempt a session recovery."""
        async with self._locks[guild_id]:
            LOGGER.info("Attempting to heal session for guild %s", guild_id)

            player = self.service.get_player(guild_id)
            if not player:
                LOGGER.warning("Cannot heal: No player found for %s", guild_id)
                return

            snapshot = await self._create_snapshot(player)
            self.snapshots[guild_id] = snapshot

            await self._hard_disconnect(guild_id, player)

            await asyncio.sleep(2.0)

            try:
                await self._restore_session(snapshot)
                LOGGER.info("Session healed successfully for guild %s", guild_id)
            except Exception:
                LOGGER.exception("Failed to heal session for %s", guild_id)
                # Fallback: Clean up if healing failed
                self.snapshots.pop(guild_id, None)

    async def _create_snapshot(self, player: MusicPlayer) -> PlayerStateSnapshot:
        """Extracts deep state from the player."""
        voice_channel_id = _get_voice_channel_id(player.channel)
        if not voice_channel_id and (vc_client := player.guild.voice_client):
            voice_channel_id = _get_voice_channel_id(vc_client.channel)

        if not voice_channel_id:
            raise ValueError("Cannot snapshot: Player has no active voice channel")

        session = self.service.sessions.get(player.guild.id)
        text_channel_id = None
        if session:
            text_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )

        # Get requester map copy
        req_map = player._requester_map.copy()  # pyright: ignore[reportPrivateUsage]
        volume = await self.service.get_volume(guild_id=player.guild.id)

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

    async def _hard_disconnect(self, guild_id: int, player: MusicPlayer):
        """Forcefully destroys the connection without triggering normal cleanup hooks."""
        try:
            # Tell Mafic/Lavalink to destroy the player
            if player.guild.voice_client:
                await player.guild.voice_client.disconnect(force=True)
        except Exception:
            LOGGER.exception("Failed to hard disconnect for guild %s", guild_id)
            pass

    async def _restore_session(self, snapshot: PlayerStateSnapshot):
        """Rebuilds the player from the snapshot."""
        guild = self.service.bot.get_guild(snapshot.guild_id)
        if not guild:
            return
        vc_channel = guild.get_channel(snapshot.voice_channel_id)

        if not vc_channel:
            LOGGER.error(
                "Cannot restore: Voice channel %s not found", snapshot.voice_channel_id
            )
            return

        if isinstance(vc_channel, (ForumChannel, TextChannel, CategoryChannel)):
            raise ValueError("Invalid channel type for restoration")
        try:
            await vc_channel.connect(cls=music_player_factory)
        except Exception as e:
            LOGGER.error("Failed to reconnect to voice: %s", e)
            return

        player = self.service.get_player(snapshot.guild_id)
        if not player:
            raise RuntimeError("Player failed to reconnect")

        # 2. Restore Internal State
        player.queue._queue.extend(snapshot.queue)  # pyright: ignore[reportPrivateUsage] # Restore queue
        player.repeat.mode = snapshot.repeat_mode
        player._requester_map = snapshot.requester_map  # pyright: ignore[reportPrivateUsage]
        await player.set_volume(snapshot.volume)
        LOGGER.debug("New player: %s", player)
        # log queue len and repeat
        LOGGER.debug(
            "Queue len: %s, Repeat mode: %s", len(player.queue), player.repeat.mode
        )
        # 3. Restore Playback
        if snapshot.current_track:
            # We explicitly play the track
            await player.play(
                snapshot.current_track,
                start_time=snapshot.position,
                volume=snapshot.volume,
                pause=snapshot.is_paused,
            )
            # Restore requester for the current track
            # (The map restore above handles queue tracks, but active track needs set_requester sometimes depending on logic)
            requester = snapshot.requester_map.get(snapshot.current_track.identifier)
            if requester:
                # Re-associate if needed, though map restore should cover it
                pass

        # 4. Restore Controller (UI)
        # We need to spawn a new controller because the old one is likely dead/zombie
        # We reuse the logic from service
        if snapshot.current_track:
            await self.service._spawn_controller(player, snapshot.current_track)  # pyright: ignore[reportPrivateUsage]
        if session := snapshot.session:
            self.service.sessions.setdefault(snapshot.guild_id, session)

        self.snapshots.pop(snapshot.guild_id, None)
