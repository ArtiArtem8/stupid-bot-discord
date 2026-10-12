"""Music listening history and session completion events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from discord.ext import commands
from discord.utils import utcnow


def main_session_channel_id(session: MusicSession) -> int | None:
    """Return the text channel with the most interactions in the session."""
    if not session.channel_usage:
        return None
    return max(session.channel_usage, key=session.channel_usage.__getitem__)


def dispatch_music_session_end(
    bot: commands.Bot, guild_id: int, session: MusicSession | None
) -> None:
    """Dispatch a completed music session when it has reportable activity."""
    if session is None or not session.tracks:
        return
    channel_id = main_session_channel_id(session)
    if channel_id is None:
        return
    bot.dispatch("music_session_end", guild_id, session, channel_id)


@dataclass(slots=True)
class TrackInfo:
    """Historical information about a played track."""

    title: str
    uri: str
    skipped: bool = False
    requester_id: int | None = None
    channel_id: int | None = None
    thumbnail_url: str | None = None
    start_timestamp: datetime | None = None
    end_timestamp: datetime = field(default_factory=utcnow)


@dataclass
class MusicSession:
    """Represents a music listening session in a guild."""

    guild_id: int
    start_time: datetime = field(default_factory=utcnow)
    tracks: list[TrackInfo] = field(default_factory=list[TrackInfo])
    channel_usage: dict[int, int] = field(default_factory=dict[int, int])
    participants: set[int] = field(default_factory=set[int])

    def record_interaction(self, channel_id: int, user_id: int) -> None:
        """Record a user interaction in a text channel."""
        self.channel_usage[channel_id] = self.channel_usage.get(channel_id, 0) + 1
        self.participants.add(user_id)

    def add_track(
        self,
        title: str,
        uri: str,
        requester_id: int | None,
        channel_id: int | None,
        skipped: bool = False,
        start_timestamp: datetime | None = None,
        thumbnail_url: str | None = None,
    ) -> None:
        """Add a track to the session."""
        track = TrackInfo(
            title=title,
            uri=uri,
            skipped=skipped,
            requester_id=requester_id,
            channel_id=channel_id,
            thumbnail_url=thumbnail_url,
            start_timestamp=start_timestamp,
            end_timestamp=utcnow(),
        )
        self.tracks.append(track)
        if requester_id is not None:
            self.participants.add(requester_id)
