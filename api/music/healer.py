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
from discord.ext import commands

from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from repositories.volume_repository import VolumeRepository

from .models import PlayerStateSnapshot
from .player import MusicPlayer, music_player_factory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _get_voice_channel_id(
    channel: VoiceChannel | StageChannel | Connectable | None,
) -> int | None:
    """Extract channel ID if it's a voice/stage channel."""
    return channel.id if isinstance(channel, VoiceChannel | StageChannel) else None


class SessionHealer:
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
        self._locks = defaultdict(asyncio.Lock)

    async def capture_and_heal(self, guild_id: int) -> None:
        """Main entry point to attempt a session recovery."""
        async with self._locks[guild_id]:
            logger.info("Attempting to heal session for guild %s", guild_id)

            player = self.connection.get_player(guild_id)
            if not player:
                logger.warning("Cannot heal: No player found for %s", guild_id)
                return

            try:
                snapshot = await self._create_snapshot(player)
                self.snapshots[guild_id] = snapshot

                await self._hard_disconnect(guild_id, player)

                await asyncio.sleep(2.0)

                await self._restore_session(snapshot)
                logger.info("Session healed successfully for guild %s", guild_id)
            except Exception:
                logger.exception("Failed to heal session for %s", guild_id)
                # Fallback: Clean up if healing failed
                self.snapshots.pop(guild_id, None)

    async def cleanup_after_disconnect(self, guild_id: int) -> None:
        """Called when bot is seemingly disconnected but we want to cleanup properly."""
        # Using connection manager logic or just minimal cleanup?
        # Similar logic to original _cleanup_after_disconnect
        # 1. Destroy Controller
        await self.ui.controller.destroy_for_guild(
            guild_id
        )  # Access controller from UI orchestrator or inject?
        # Note: UIOrchestrator has controller.

        # 2. End Session
        session = self.state.end_session(guild_id)
        if session and session.tracks:
            # Find the main channel for this session
            main_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )
            if main_channel_id:
                # Dispatch the event to the bot so the cog can handle it
                self.bot.dispatch(
                    "music_session_end", guild_id, session, main_channel_id
                )

        # 3. Clear Timer
        self.state.cancel_timer(guild_id)

        # 4. Clear internal
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

        # Get requester map copy
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
        """Forcefully destroys the connection without triggering normal cleanup hooks."""
        try:
            # Tell Mafic/Lavalink to destroy the player
            if player.guild.voice_client:
                await player.guild.voice_client.disconnect(force=True)
        except Exception:
            logger.exception("Failed to hard disconnect for guild %s", guild_id)
            pass

    async def _restore_session(self, snapshot: PlayerStateSnapshot) -> None:
        """Rebuilds the player from the snapshot."""
        guild = self.bot.get_guild(snapshot.guild_id)
        if not guild:
            return
        vc_channel = guild.get_channel(snapshot.voice_channel_id)

        if not vc_channel:
            logger.error(
                "Cannot restore: Voice channel %s not found", snapshot.voice_channel_id
            )
            return

        if isinstance(vc_channel, (ForumChannel, TextChannel, CategoryChannel)):
            raise ValueError("Invalid channel type for restoration")
        try:
            # We can use ConnectionManager join, OR raw connect.
            # Raw connect is safer for restoration to avoid side effects of 'join' logic?
            # Original used raw connect.
            await vc_channel.connect(cls=music_player_factory)
        except Exception as e:
            logger.error("Failed to reconnect to voice: %s", e)
            return

        player = self.connection.get_player(snapshot.guild_id)
        if not player:
            raise RuntimeError("Player failed to reconnect")

        # 2. Restore Internal State
        player.queue._queue.extend(snapshot.queue)  # pyright: ignore[reportPrivateUsage]
        player.repeat.mode = snapshot.repeat_mode
        player._requester_map = snapshot.requester_map  # pyright: ignore[reportPrivateUsage]
        await player.set_volume(snapshot.volume)
        logger.debug("New player: %s", player)
        logger.debug(
            "Queue len: %s, Repeat mode: %s", len(player.queue), player.repeat.mode
        )
        # 3. Restore Playback
        if snapshot.current_track:
            await player.play(
                snapshot.current_track,
                start_time=snapshot.position,
                volume=snapshot.volume,
                pause=snapshot.is_paused,
            )

        # 4. Restore Controller (UI)
        if snapshot.current_track:
            await self.ui.spawn_controller(player, snapshot.current_track)
        if session := snapshot.session:
            self.state.sessions.setdefault(snapshot.guild_id, session)

        self.snapshots.pop(snapshot.guild_id, None)
