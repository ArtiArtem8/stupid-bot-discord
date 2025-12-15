"""Models and data structures for the music module."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Literal, Protocol, Self, TypedDict

if TYPE_CHECKING:
    import discord

    from .player import MusicPlayer, Track
import discord
import mafic
from discord.utils import utcnow

logger = logging.getLogger(__name__)

type Track = mafic.Track
type Playlist = mafic.Playlist
type SearchResult = list[Track] | Playlist | None


class MusicError(Exception):
    """Base exception for Music API errors."""


class NodeNotConnectedError(MusicError):
    """Raised when Lavalink node is not connected."""


class PlaylistResponseData(TypedDict):
    """Data for playlist add event."""

    type: Literal["playlist"]
    playlist: Playlist


class TrackResponseData(TypedDict):
    """Data for track play event."""

    type: Literal["track"]
    track: Track
    playing: bool


type PlayResponseData = PlaylistResponseData | TrackResponseData


class SkipTrackData(TypedDict):
    """Data for skip event."""

    before: Track | None
    after: Track | None


class RepeatModeData(TypedDict):
    """Data for repeat mode change."""

    mode: str
    previous: str


class RotateTrackData(TypedDict):
    """Data for rotate event."""

    skipped: Track | None
    next: Track | None


class MusicResultStatus(StrEnum):
    """Status of a music operation."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ERROR = "ERROR"


class RepeatMode(StrEnum):
    """Repeat modes."""

    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class VoiceCheckResult(StrEnum):
    """Result of voice channel checks."""

    ALREADY_CONNECTED = auto()
    CHANNEL_EMPTY = auto()
    CONNECTION_FAILED = auto()
    INVALID_CHANNEL_TYPE = auto()
    MOVED_CHANNELS = auto()
    SUCCESS = auto()
    USER_NOT_IN_VOICE = auto()
    USER_NOT_MEMBER = auto()

    @property
    def status(self) -> MusicResultStatus:
        """Convert voice result to music result status."""
        match self:
            case (
                VoiceCheckResult.ALREADY_CONNECTED
                | VoiceCheckResult.MOVED_CHANNELS
                | VoiceCheckResult.SUCCESS
            ):
                return MusicResultStatus.SUCCESS
            case VoiceCheckResult.CONNECTION_FAILED:
                return MusicResultStatus.ERROR
            case _:
                return MusicResultStatus.FAILURE


type VoiceJoinResult = tuple[VoiceCheckResult, discord.abc.GuildChannel | None]


@dataclass(slots=True)
class TrackInfo:
    """Historical information about a played track."""

    title: str
    uri: str
    skipped: bool = False
    requester_id: int | None = None
    channel_id: int | None = None
    timestamp: datetime = field(default_factory=utcnow)


@dataclass(slots=True, frozen=True)
class TrackId:
    id: str

    @classmethod
    def from_track(cls, track: Track) -> Self:
        """Create TrackId from a Track object."""
        _id = track.identifier
        return cls(_id)

    @classmethod
    def from_any(cls, id: int | str | Track) -> Self:
        """Universal constructor that accepts multiple types."""
        if isinstance(id, mafic.Track):
            return cls.from_track(id)
        return cls(str(id))


@dataclass(frozen=True, slots=True)
class TrackRequester:
    """Information about who requested a track and where."""

    user_id: int
    channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class TrackGroup:
    """Helper class to group consecutive tracks."""

    title: str
    uri: str
    skipped: bool
    count: int


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
        timestamp: datetime | None = None,
    ) -> None:
        """Add a track to the session."""
        track = TrackInfo(
            title=title,
            uri=uri,
            skipped=skipped,
            requester_id=requester_id,
            channel_id=channel_id,
            timestamp=utcnow(),
        )
        self.tracks.append(track)
        self.participants.add(requester_id or 0)

    # def get_user_tracks(self, user_id: int) -> list[TrackInfo]:
    #     """Get all user tracks."""
    #     return [t for t in self.tracks if t.requester_id == user_id]

    # def get_channel_tracks(self, channel_id: int) -> list[TrackInfo]:
    #     """Get all tracks from a channel."""
    #     return [t for t in self.tracks if t.channel_id == channel_id]

    # def get_user_stats(self, user_id: int) -> dict[str, int]:
    #     """Get user statistics."""
    #     user_tracks = self.get_user_tracks(user_id)
    #     return {
    #         "total": len(user_tracks),
    #         "played": sum(1 for t in user_tracks if not t.skipped),
    #         "skipped": sum(1 for t in user_tracks if t.skipped),
    #     }

    # def get_top_contributors(self, limit: int = 5) -> list[tuple[int, int]]:
    #     """Get top contributors by track count."""
    #     user_counts = Counter(
    #         t.requester_id for t in self.tracks if t.requester_id is not None
    #     )
    #     return user_counts.most_common(limit)


@dataclass(frozen=True, slots=True)
class MusicResult[T]:
    """Result of a music operation."""

    status: MusicResultStatus
    message: str
    data: T | None = None

    @property
    def is_success(self) -> bool:
        """Check if the operation was successful."""
        return self.status is MusicResultStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Snapshot of the current queue state."""

    current: Track | None
    queue: tuple[Track, ...]
    repeat_mode: RepeatMode


@dataclass(slots=True)
class PlayerStateSnapshot:
    """Complete state of a player for restoration."""

    guild_id: int
    voice_channel_id: int
    text_channel_id: int | None

    # Track State
    current_track: Track | None
    position: int
    is_paused: bool
    volume: int

    # Queue State
    queue: list[Track]
    repeat_mode: RepeatMode

    # Filters
    filters: mafic.Filter | None

    # Requester Map (Crucial for history/permissions)
    requester_map: dict[str, TrackRequester]
    session: MusicSession | None


class ControllerManagerProtocol(Protocol):
    """Protocol for controller management."""

    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
        track: Track,
    ) -> None:
        """Create a controller for a user."""
        ...

    async def destroy_for_guild(self, guild_id: int) -> None:
        """Destroy controller for a guild."""
        ...
