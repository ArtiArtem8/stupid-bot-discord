from __future__ import annotations

import datetime
import logging
import time
from typing import TypedDict

import mafic
from discord.utils import utcnow

from api.music.models import MusicSession, PlaybackAttempt

logger = logging.getLogger(__name__)


class EmptyTimerInfo(TypedDict):
    timestamp: float
    reason: str | None


class StateManager:
    """Manages music sessions, history recording, and auto-leave timers."""

    def __init__(self) -> None:
        self.sessions: dict[int, MusicSession] = {}
        self._track_start_times_dt: dict[tuple[int, int], datetime.datetime] = {}

        # Auto-leave tracking
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}

    def get_session(self, guild_id: int) -> MusicSession | None:
        return self.sessions.get(guild_id)

    def get_or_create_session(self, guild_id: int) -> MusicSession:
        return self.sessions.setdefault(guild_id, MusicSession(guild_id=guild_id))

    def end_session(self, guild_id: int) -> MusicSession | None:
        """Removes and returns the session for a guild."""
        session = self.sessions.pop(guild_id, None)
        self.clear_track_start_times(guild_id)
        return session

    def clear_track_start_times(self, guild_id: int) -> None:
        """Remove unfinished playback start records for one guild."""
        self._track_start_times_dt = {
            key: value
            for key, value in self._track_start_times_dt.items()
            if key[0] != guild_id
        }

    def record_track_start(self, guild_id: int, attempt: PlaybackAttempt) -> None:
        self.get_or_create_session(guild_id)
        # Using utcnow() as in original
        self._track_start_times_dt[(guild_id, attempt.attempt_id)] = utcnow()

    def record_history(
        self, guild_id: int, attempt: PlaybackAttempt, reason: mafic.EndReason
    ) -> None:
        session = self.sessions.get(guild_id)
        start_time = self._track_start_times_dt.pop(
            (guild_id, attempt.attempt_id), None
        )

        if not session or not start_time:
            return

        skipped = reason in (
            mafic.EndReason.STOPPED,
            mafic.EndReason.REPLACED,
            mafic.EndReason.LOAD_FAILED,
            mafic.EndReason.CLEANUP,
        )

        track = attempt.entry.track
        requester_info = attempt.entry.requester

        session.add_track(
            title=track.title,
            uri=track.uri or "",
            requester_id=requester_info.user_id if requester_info else None,
            channel_id=requester_info.channel_id if requester_info else None,
            skipped=skipped,
            start_timestamp=start_time,
            thumbnail_url=track.artwork_url,
        )
        logger.debug("Recorded history: %s (Skipped: %s)", track.title, skipped)

    def is_timer_active(self, guild_id: int) -> bool:
        return guild_id in self.empty_channel_timers

    def start_timer(self, guild_id: int, reason: str | None) -> None:
        if guild_id not in self.empty_channel_timers:
            self.empty_channel_timers[guild_id] = EmptyTimerInfo(
                timestamp=time.monotonic(),
                reason=reason,
            )

    def cancel_timer(self, guild_id: int) -> None:
        self.empty_channel_timers.pop(guild_id, None)

    def get_expired_timers(
        self, timeout_duration: float
    ) -> list[tuple[int, str | None]]:
        """Return list of (guild_id, reason) for expired timers."""
        expired: list[tuple[int, str | None]] = []
        current_time = time.monotonic()
        for guild_id, info in self.empty_channel_timers.items():
            if current_time - info["timestamp"] > timeout_duration:
                expired.append((guild_id, info["reason"]))
        return expired

    def clear_expired_timers(self, expired_guild_ids: list[int]) -> None:
        """Clear the specified expired timers."""
        for guild_id in expired_guild_ids:
            self.empty_channel_timers.pop(guild_id, None)

    async def check_auto_leave(self) -> list[int]:
        """Check for guilds that have been empty for too long."""
        import config

        expired_timers = self.get_expired_timers(config.MUSIC_AUTO_LEAVE_TIMEOUT)
        expired_guild_ids = [guild_id for guild_id, _ in expired_timers]

        for guild_id, reason in expired_timers:
            logger.info(f"Auto-leave timer expired for guild {guild_id} ({reason})")

        return expired_guild_ids
