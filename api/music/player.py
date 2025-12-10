"""Custom Music Player extending Mafic Player."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, override

import discord
import mafic
from discord.utils import MISSING

from .models import RepeatMode, Track, TrackId, TrackRequester
from .queue import QueueManager, RepeatManager

if TYPE_CHECKING:
    from discord.abc import Connectable

LOGGER = logging.getLogger(__name__)


class MusicPlayer(mafic.Player[discord.Client]):
    """Custom Mafic Player with Queue, Repeat, and Requester tracking."""

    def __init__(self, client: discord.Client, channel: Connectable) -> None:
        super().__init__(client, channel)

        self.queue = QueueManager()
        self.repeat = RepeatManager()
        self._requester_map: dict[str, TrackRequester] = {}

    async def move_to(
        self, channel: discord.abc.Snowflake | None, *, timeout: float = 30.0
    ) -> None:
        """Move to a different voice channel."""
        if channel is None:
            await self.disconnect()
            return

        if not isinstance(self.channel, (discord.VoiceChannel, discord.StageChannel)):
            msg = "Voice channel must be a VoiceChannel or StageChannel."
            raise TypeError(msg)

        if self.channel and channel.id == self.channel.id:
            return

        self._voice_state_update_event.clear()
        self._voice_server_update_event.clear()

        await self.guild.change_voice_state(channel=channel)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._voice_state_update_event.wait(),
                    self._voice_server_update_event.wait(),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Timed out moving to channel %s in guild %s",
                channel.id,
                self.guild.id,
            )
            raise

    def set_requester(
        self, track: Track, requester_id: int, channel_id: int | None = None
    ) -> None:
        """Associate a requester with a track."""
        LOGGER.debug("Setting requester for track %s", track.id)
        _id = TrackId.from_track(track).id
        self._requester_map[_id] = TrackRequester(
            user_id=requester_id,
            channel_id=channel_id,
        )

    def get_requester(self, track: Track) -> TrackRequester | None:
        """Get the requester ID for a track."""
        return self._requester_map.get(TrackId.from_track(track).id)

    def clear_queue(self) -> None:
        """Clear the queue and requester map."""
        self.queue.clear()
        self._requester_map.clear()

    def clear_state(self) -> None:
        """Clear queue and state."""
        self.clear_queue()

    async def advance(
        self,
        *,
        force_skip: bool = False,
        previous_track: Track | None = None,
    ) -> Track | None:
        """Advance to the next state (next track or repeat).

        :param force_skip: If True, ignores RepeatMode.TRACK and moves to next song.
        """
        current_or_prev = previous_track or self.current

        LOGGER.debug(
            "Advancing in guild %s, current track: %s", self.guild.id, current_or_prev
        )
        LOGGER.debug("queue: %s, repeat mode: %s", self.queue, self.repeat.mode)

        if not force_skip and self.repeat.mode is RepeatMode.TRACK and current_or_prev:
            LOGGER.debug("Repeating track %s", current_or_prev)
            await self.play(current_or_prev, start_time=0)
            return current_or_prev

        if not force_skip and self.repeat.mode is RepeatMode.QUEUE and current_or_prev:
            LOGGER.debug("Adding track %s to queue", current_or_prev)
            self.queue.add(current_or_prev)

        next_track = self.queue.pop_next()
        if not next_track:
            LOGGER.debug("No next track in queue, stopping")
            await self.stop()
            return None

        LOGGER.debug("Playing next track: %s", next_track)
        await self.play(next_track, start_time=0)
        return next_track

    async def skip(self) -> Track | None:
        """Skip the current track.
        This forces the player to advance to the next track, ignoring RepeatMode.TRACK.
        """
        skipped_track = self.current

        next_track = await self.advance(force_skip=True)

        if not next_track:
            await self.stop()

        return skipped_track

    @override
    async def update(
        self,
        *,
        track: Track | str | None = MISSING,
        position: int | None = None,
        end_time: int | None = None,
        volume: int | None = None,
        pause: bool | None = None,
        filter: mafic.Filter | None = None,
        replace: bool = False,
    ) -> None:
        try:
            return await super().update(
                track=track,
                position=position,
                end_time=end_time,
                volume=volume,
                pause=pause,
                filter=filter,
                replace=replace,
            )
        except mafic.errors.HTTPNotFound as e:
            LOGGER.exception("Failed to update player: %s", e)
            await self.disconnect(force=True)


def music_player_factory(
    client: discord.Client, connectable: Connectable
) -> MusicPlayer:
    """Create a custom Mafic Player."""
    return MusicPlayer(client, connectable)
