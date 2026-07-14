"""Custom Music Player extending Mafic Player."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
import mafic

from .models import QueuePlacement, RepeatMode, Track, TrackId, TrackRequester
from .queue import QueueManager, RepeatManager

if TYPE_CHECKING:
    from discord.abc import Connectable

logger = logging.getLogger(__name__)


class MusicPlayer(mafic.Player[discord.Client]):
    """Custom Mafic Player with Queue, Repeat, and Requester tracking."""

    def __init__(self, client: discord.Client, channel: Connectable) -> None:
        super().__init__(client, channel)

        self.queue = QueueManager()
        self.repeat = RepeatManager()
        self._requester_map: dict[str, TrackRequester] = {}
        self._transition_lock = asyncio.Lock()
        self._is_stale = False

    @property
    def is_stale(self) -> bool:
        """Return whether this player has been detached from the active lifecycle."""
        return self._is_stale

    def mark_stale(self) -> None:
        """Mark this player as no longer safe for reuse."""
        self._is_stale = True

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
            logger.warning(
                "Timed out moving to channel %s in guild %s",
                channel.id,
                self.guild.id,
            )
            raise

    def set_requester(
        self, track: Track, requester_id: int, channel_id: int | None = None
    ) -> None:
        """Associate a requester with a track."""
        logger.debug("Setting requester for track %s", track.identifier)
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
        logger.debug("Cleared queue & map for guild %s", self.guild.id)

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
        async with self._transition_lock:
            return await self._advance_unlocked(
                force_skip=force_skip,
                previous_track=previous_track,
            )

    async def advance_after_end(
        self,
        previous_track: Track,
        *,
        force_skip: bool = False,
    ) -> Track | None:
        """Advance after Lavalink reports a natural track end."""
        async with self._transition_lock:
            if self.current is not None:
                if force_skip and self.current is previous_track:
                    return await self._advance_unlocked(
                        force_skip=True,
                        previous_track=previous_track,
                    )
                return await self._handle_stale_end_unlocked(
                    previous_track,
                    force_skip=force_skip,
                )
            return await self._advance_unlocked(
                force_skip=force_skip,
                previous_track=previous_track,
            )

    async def _advance_unlocked(
        self,
        *,
        force_skip: bool = False,
        previous_track: Track | None = None,
    ) -> Track | None:
        """Advance while the caller holds the transition lock."""
        current_or_prev = previous_track or self.current

        logger.debug(
            "Advancing in guild %s, current track: %s", self.guild.id, current_or_prev
        )
        logger.debug("queue: %s, repeat mode: %s", self.queue, self.repeat.mode)

        repeat_track = await self._apply_repeat_unlocked(
            current_or_prev,
            force_skip=force_skip,
        )
        if repeat_track is not None:
            return repeat_track

        next_track = self.queue.pop_next()
        if not next_track:
            logger.debug("No next track in queue, stopping")
            await self.stop()
            return None

        logger.debug("Playing next track: %s", next_track)
        await self.play(next_track, start_time=0, pause=False)
        return next_track

    async def _handle_stale_end_unlocked(
        self,
        previous_track: Track,
        *,
        force_skip: bool = False,
    ) -> Track | None:
        logger.debug(
            "Handling stale track end in guild %s for track %s; current is %s",
            self.guild.id,
            previous_track,
            self.current,
        )
        if force_skip:
            return None
        if self.repeat.mode is RepeatMode.TRACK:
            replacement_track = self.current
            if replacement_track is not None:
                self.queue.prepend(replacement_track)
            await self.play(previous_track, start_time=0, pause=False)
            return previous_track
        elif self.repeat.mode is RepeatMode.QUEUE:
            self.queue.append(previous_track)
        return None

    async def _apply_repeat_unlocked(
        self,
        current_or_prev: Track | None,
        *,
        force_skip: bool,
    ) -> Track | None:
        if force_skip or current_or_prev is None:
            return None
        if self.repeat.mode is RepeatMode.TRACK:
            logger.debug("Repeating track %s", current_or_prev)
            await self.play(current_or_prev, start_time=0, pause=False)
            return current_or_prev
        if self.repeat.mode is RepeatMode.QUEUE:
            logger.debug("Adding track %s to queue", current_or_prev)
            self.queue.append(current_or_prev)
        return None

    async def enqueue_tracks(
        self,
        tracks: Sequence[Track],
        *,
        placement: QueuePlacement,
    ) -> Track | None:
        """Add tracks to the queue and start playback if the player is idle."""
        if not tracks:
            return None

        async with self._transition_lock:
            match placement:
                case "end":
                    self.queue.extend(tracks)
                case "next":
                    self.queue.extend_front(tracks)

            if self.current is None:
                return await self._advance_unlocked()
            return None

    async def skip(self) -> tuple[Track | None, Track | None]:
        """Skip the current track.
        This forces the player to advance to the next track, ignoring RepeatMode.TRACK.
        """
        async with self._transition_lock:
            skipped_track = self.current
            started_track = await self._advance_unlocked(
                force_skip=True,
                previous_track=skipped_track,
            )
            return skipped_track, started_track

    async def rotate_current(self) -> tuple[Track | None, Track | None]:
        """Move the current track to the end and advance atomically."""
        async with self._transition_lock:
            moved_track = self.current
            if moved_track is None:
                return None, None
            self.queue.append(moved_track)
            started_track = await self._advance_unlocked(
                force_skip=True,
                previous_track=moved_track,
            )
            return moved_track, started_track

    async def start_queued_if_idle(self) -> Track | None:
        """Start the next queued track when playback is idle."""
        async with self._transition_lock:
            if self.current is not None:
                return None

            next_track = self.queue.pop_next()
            if next_track is None:
                return None

            await self.play(next_track, start_time=0, pause=False)
            return next_track

    async def stop_and_clear(self) -> None:
        """Clear queued state and stop playback atomically."""
        async with self._transition_lock:
            self.clear_queue()
            await super().stop()


def music_player_factory(
    client: discord.Client, connectable: Connectable
) -> MusicPlayer:
    """Create a custom Mafic Player."""
    return MusicPlayer(client, connectable)
