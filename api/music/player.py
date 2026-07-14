"""Custom Music Player extending Mafic Player."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
import mafic

from .models import (
    PlaybackAttempt,
    QueueEntry,
    QueuePlacement,
    RepeatMode,
    Track,
    TrackEndOutcome,
    TrackRequester,
)
from .queue import QueueManager, RepeatManager

if TYPE_CHECKING:
    from discord.abc import Connectable

logger = logging.getLogger(__name__)


def tracks_match(left: Track, right: Track) -> bool:
    """Compare the source identity Mafic exposes for playback events."""
    return left.source == right.source and left.identifier == right.identifier


class MusicPlayer(mafic.Player[discord.Client]):
    """Mafic player owning queue-entry and playback-attempt transitions."""

    def __init__(self, client: discord.Client, channel: Connectable) -> None:
        super().__init__(client, channel)
        self.queue = QueueManager()
        self.repeat = RepeatManager()
        self._next_entry_id = 1
        self._next_attempt_id = 1
        self._current_attempt: PlaybackAttempt | None = None
        self._pending_end_attempts: deque[PlaybackAttempt] = deque()
        self._exception_attempt_ids: set[int] = set()
        self._transition_lock = asyncio.Lock()
        self._is_stale = False

    @property
    def current_attempt(self) -> PlaybackAttempt | None:
        return self._current_attempt

    @property
    def current_entry(self) -> QueueEntry | None:
        attempt = self._current_attempt
        return attempt.entry if attempt is not None else None

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

    def clear_queue(self) -> None:
        self.queue.clear()
        logger.debug("Cleared queue for guild %s", self.guild.id)

    def clear_state(self) -> None:
        self.clear_queue()

    def queue_snapshot(self) -> tuple[QueueEntry, ...]:
        return self.queue.snapshot()

    def resolve_current_attempt(self, track: Track) -> PlaybackAttempt | None:
        """Resolve TrackStart against the best identity Mafic makes available."""
        attempt = self._current_attempt
        if attempt is not None and tracks_match(attempt.entry.track, track):
            return attempt
        return None

    async def resolve_exception_attempt(self, track: Track) -> PlaybackAttempt | None:
        """Resolve exception/stuck events current-first, then pending FIFO."""
        async with self._transition_lock:
            return self._resolve_exception_attempt_unlocked(track)

    async def claim_track_exception(self, track: Track) -> PlaybackAttempt | None:
        """Resolve and deduplicate an exception for a live attempt."""
        async with self._transition_lock:
            attempt = self._resolve_exception_attempt_unlocked(track)
            if attempt is None or attempt.attempt_id in self._exception_attempt_ids:
                return None
            self._exception_attempt_ids.add(attempt.attempt_id)
            return attempt

    def _resolve_exception_attempt_unlocked(
        self, track: Track
    ) -> PlaybackAttempt | None:
        # Without a playback event token, equal source playbacks cannot be
        # distinguished absolutely. Exceptions prefer the current attempt.
        current = self.resolve_current_attempt(track)
        if current is not None:
            return current
        for attempt in self._pending_end_attempts:
            if tracks_match(attempt.entry.track, track):
                return attempt
        return None

    def _new_entry(self, track: Track, requester: TrackRequester | None) -> QueueEntry:
        entry = QueueEntry(self._next_entry_id, track, requester)
        self._next_entry_id += 1
        return entry

    async def _start_entry_unlocked(
        self,
        entry: QueueEntry,
        *,
        start_time: int = 0,
        volume: int | None = None,
        pause: bool = False,
    ) -> PlaybackAttempt:
        """Create and start one attempt while the transition lock is held."""
        previous = self._current_attempt
        attempt = PlaybackAttempt(self._next_attempt_id, entry)
        self._next_attempt_id += 1
        self._current_attempt = attempt
        try:
            await self.play(
                entry.track,
                start_time=start_time,
                volume=volume,
                pause=pause,
            )
        except (Exception, asyncio.CancelledError):
            self._current_attempt = previous
            raise
        return attempt

    async def enqueue_tracks(
        self,
        tracks: Sequence[Track],
        requester: TrackRequester | None,
        *,
        placement: QueuePlacement,
    ) -> PlaybackAttempt | None:
        """Create entries, enqueue them, and start one if idle."""
        if not tracks:
            return None
        async with self._transition_lock:
            entries = tuple(self._new_entry(track, requester) for track in tracks)
            if placement == "end":
                self.queue.extend(entries)
            else:
                self.queue.extend_front(entries)
            if self._current_attempt is not None:
                return None
            queued_state = self.queue.snapshot()
            entry = self.queue.pop_next()
            if entry is None:
                return None
            try:
                return await self._start_entry_unlocked(entry)
            except (Exception, asyncio.CancelledError):
                self.queue.restore(queued_state)
                raise

    async def skip(
        self,
    ) -> tuple[PlaybackAttempt | None, PlaybackAttempt | None]:
        """Replace current playback with the next entry, ignoring repeat."""
        async with self._transition_lock:
            ended = self._current_attempt
            if ended is None:
                return None, None
            old_queue = self.queue.snapshot()
            old_pending = self._pending_end_attempts.copy()
            self._pending_end_attempts.append(ended)
            self._current_attempt = None
            next_entry = self.queue.pop_next()
            try:
                if next_entry is None:
                    await super().stop()
                    return ended, None
                started = await self._start_entry_unlocked(next_entry)
                return ended, started
            except (Exception, asyncio.CancelledError):
                self._current_attempt = ended
                self._pending_end_attempts = old_pending
                self.queue.restore(old_queue)
                raise

    async def rotate_current(
        self,
    ) -> tuple[PlaybackAttempt | None, PlaybackAttempt | None]:
        """Move the current entry to the queue tail and start the next entry."""
        async with self._transition_lock:
            ended = self._current_attempt
            if ended is None:
                return None, None
            old_queue = self.queue.snapshot()
            old_pending = self._pending_end_attempts.copy()
            self.queue.append(ended.entry)
            self._pending_end_attempts.append(ended)
            self._current_attempt = None
            next_entry = self.queue.pop_next()
            try:
                if next_entry is None:
                    return ended, None
                started = await self._start_entry_unlocked(next_entry)
                return ended, started
            except (Exception, asyncio.CancelledError):
                self._current_attempt = ended
                self._pending_end_attempts = old_pending
                self.queue.restore(old_queue)
                raise

    async def stop_and_clear(self) -> None:
        """Clear queued state and stop playback atomically."""
        async with self._transition_lock:
            old_queue = self.queue.snapshot()
            old_pending = self._pending_end_attempts.copy()
            ended = self._current_attempt
            self.queue.clear()
            if ended is not None:
                self._pending_end_attempts.append(ended)
                self._current_attempt = None
            try:
                await super().stop()
            except (Exception, asyncio.CancelledError):
                self.queue.restore(old_queue)
                self._pending_end_attempts = old_pending
                self._current_attempt = ended
                raise

    async def start_queued_if_idle(self) -> PlaybackAttempt | None:
        """Start one queued entry if no attempt is active."""
        async with self._transition_lock:
            return await self._start_queued_if_idle_unlocked()

    async def _start_queued_if_idle_unlocked(self) -> PlaybackAttempt | None:
        # Pending attempts only attribute delayed events. The current attempt
        # alone determines whether this player is busy.
        if self._current_attempt is not None:
            return None
        entry = self.queue.pop_next()
        if entry is None:
            return None
        try:
            return await self._start_entry_unlocked(entry)
        except (Exception, asyncio.CancelledError):
            self.queue.prepend(entry)
            raise

    async def handle_track_end(
        self, track: Track, reason: mafic.EndReason
    ) -> TrackEndOutcome:
        """Classify one Mafic end event and perform any required transition."""
        async with self._transition_lock:
            old_queue = self.queue.snapshot()
            old_pending = self._pending_end_attempts.copy()
            old_current = self._current_attempt

            pending = self._pop_pending_match_unlocked(track)
            if pending is not None:
                self._exception_attempt_ids.discard(pending.attempt_id)
                return TrackEndOutcome(pending, None, False)

            current = self.resolve_current_attempt(track)
            if current is None:
                return TrackEndOutcome(None, None, True)

            self._current_attempt = None
            try:
                started = await self._transition_after_current_end_unlocked(
                    current, reason
                )
            except (Exception, asyncio.CancelledError):
                self.queue.restore(old_queue)
                self._pending_end_attempts = old_pending
                self._current_attempt = old_current
                raise
            self._exception_attempt_ids.discard(current.attempt_id)
            return TrackEndOutcome(current, started, False)

    def _pop_pending_match_unlocked(self, track: Track) -> PlaybackAttempt | None:
        # TrackEnd is pending-first and FIFO. Without a Lavalink event token,
        # equal source playbacks cannot be distinguished absolutely.
        for index, attempt in enumerate(self._pending_end_attempts):
            if tracks_match(attempt.entry.track, track):
                del self._pending_end_attempts[index]
                return attempt
        return None

    async def _transition_after_current_end_unlocked(
        self, ended: PlaybackAttempt, reason: mafic.EndReason
    ) -> PlaybackAttempt | None:
        match reason:
            case mafic.EndReason.FINISHED:
                if self.repeat.mode is RepeatMode.TRACK:
                    return await self._start_entry_unlocked(ended.entry)
                if self.repeat.mode is RepeatMode.QUEUE:
                    self.queue.append(ended.entry)
                return await self._start_queued_if_idle_unlocked()
            case mafic.EndReason.LOAD_FAILED:
                return await self._start_queued_if_idle_unlocked()
            case (
                mafic.EndReason.STOPPED
                | mafic.EndReason.REPLACED
                | mafic.EndReason.CLEANUP
            ):
                return None

    async def invalidate_if_current_attempt(self, expected: PlaybackAttempt) -> bool:
        """Atomically claim an expected attempt for player teardown."""
        async with self._transition_lock:
            if self._current_attempt is not expected:
                return False
            self._current_attempt = None
            self._exception_attempt_ids.discard(expected.attempt_id)
            self._is_stale = True
            return True

    async def seek_attempt(self, expected: PlaybackAttempt, position: int) -> bool:
        """Seek only while the expected attempt owns this player."""
        async with self._transition_lock:
            if self._is_stale or self._current_attempt is not expected:
                return False
            await self.seek(position)
            return self._current_attempt is expected and not self._is_stale

    async def restore_attempt_state(
        self,
        expected: PlaybackAttempt,
        *,
        volume: int,
        pause: bool,
    ) -> bool:
        """Restore playback state only for the expected attempt."""
        async with self._transition_lock:
            if self._is_stale or self._current_attempt is not expected:
                return False
            await self.update(volume=volume, pause=pause)
            return self._current_attempt is expected and not self._is_stale

    def restore_entries(
        self, current: QueueEntry | None, queued: Sequence[QueueEntry]
    ) -> None:
        """Restore entry identity into a fresh runtime player."""
        self.queue.restore(queued)
        max_id = max(
            (entry.entry_id for entry in (*queued, current) if entry is not None),
            default=0,
        )
        self._next_entry_id = max_id + 1

    async def restore_playback(
        self,
        entry: QueueEntry,
        *,
        start_time: int,
        volume: int,
        pause: bool,
    ) -> PlaybackAttempt:
        """Create a fresh runtime attempt for a restored queue entry."""
        async with self._transition_lock:
            previous = self._current_attempt
            self._current_attempt = None
            try:
                started = await self._start_entry_unlocked(
                    entry,
                    start_time=start_time,
                    volume=volume,
                    pause=pause,
                )
            except (Exception, asyncio.CancelledError):
                self._current_attempt = previous
                raise
            if previous is not None:
                self._exception_attempt_ids.discard(previous.attempt_id)
            return started


def music_player_factory(
    client: discord.Client, connectable: Connectable
) -> MusicPlayer:
    """Create a custom Mafic Player."""
    return MusicPlayer(client, connectable)
