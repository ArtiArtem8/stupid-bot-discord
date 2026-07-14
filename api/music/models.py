"""Models and data structures for the music module."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from typing import Literal, NotRequired, Self, TypedDict, TypeVar

import discord
import mafic
from discord.utils import utcnow

logger = logging.getLogger(__name__)

MUSIC_SERVICE_UNAVAILABLE_MESSAGE = (
    "Музыкальный сервис сейчас недоступен. Попробуйте позже."
)

type Track = mafic.Track
type Playlist = mafic.Playlist
type SearchResult = list[Track] | Playlist | None
type QueuePlacement = Literal["end", "next"]
type PlayPlacement = Literal["now", "end", "next"]


class MusicError(Exception):
    """Base exception for Music API errors."""


class NodeNotConnectedError(MusicError):
    """Raised when Lavalink node is not connected."""


class PlaylistResponseData(TypedDict):
    """Data for playlist add event."""

    type: Literal["playlist"]
    playlist: Playlist
    placement: PlayPlacement
    connection: NotRequired[VoiceJoinResult]


class TrackResponseData(TypedDict):
    """Data for track play event."""

    type: Literal["track"]
    track: Track
    placement: PlayPlacement
    connection: NotRequired[VoiceJoinResult]


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
    MUSIC_SERVICE_UNAVAILABLE = auto()
    TIMEOUT = auto()
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


class ControllerDestroyReason(StrEnum):
    """Reasons that an active controller is no longer valid."""

    TRACK_END = "track_end"
    TRACK_CHANGED = "track_changed"
    TRACK_EXCEPTION = "track_exception"
    TRACK_STUCK = "track_stuck"
    VOICE_DISCONNECT = "voice_disconnect"
    PLAYER_ERROR = "player_error"
    MESSAGE_DELETED = "message_deleted"
    MANUAL_STOP = "manual_stop"
    SKIP = "skip"
    TIMEOUT = "timeout"
    STALE_VIEW = "stale_view"


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


@dataclass(slots=True, frozen=True)
class TrackId:
    id: str

    @classmethod
    def from_track(cls, track: mafic.Track) -> Self:
        """Create TrackId from a Track object."""
        _id = track.identifier
        return cls(_id)

    @classmethod
    def from_any(cls, id: int | str | mafic.Track) -> Self:
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
class QueueEntry:
    """One concrete request for a source track."""

    entry_id: int
    track: Track
    requester: TrackRequester | None


@dataclass(frozen=True, slots=True)
class PlaybackAttempt:
    """One runtime playback start for a queue entry."""

    attempt_id: int
    entry: QueueEntry


@dataclass(frozen=True, slots=True)
class TrackEndOutcome:
    """Classification and transition result for a Mafic track-end event."""

    ended_attempt: PlaybackAttempt | None
    started_attempt: PlaybackAttempt | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class TrackGroup:
    """Helper class to group consecutive tracks."""

    title: str
    uri: str
    skipped: bool
    count: int


@dataclass(frozen=True, slots=True)
class TrackExceptionPayload:
    """Payload for track exception events dispatched to the bot layer."""

    guild_id: int
    track: Track
    reason: str
    severity: str | None
    requester_id: int | None
    channel_id: int | None


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
            end_timestamp=utcnow(),  # Timestamp when the track has ended
        )
        self.tracks.append(track)
        self.participants.add(requester_id or 0)


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


T = TypeVar("T")


def player_fail_result(
    guild_id: int | None = None, *, context: str | None = None
) -> MusicResult[T]:
    """Create a standardized failure result when a music player is missing."""
    if guild_id is not None:
        suffix = f" ({context})" if context else ""
        logger.debug("No player for guild_id=%s%s", guild_id, suffix)
    return MusicResult(MusicResultStatus.FAILURE, "No player")


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Snapshot of the current queue state."""

    current: QueueEntry | None
    queue: tuple[QueueEntry, ...]
    repeat_mode: RepeatMode


@dataclass(slots=True)
class PlayerStateSnapshot:
    """Complete state of a player for restoration."""

    guild_id: int
    voice_channel_id: int
    text_channel_id: int | None

    current_entry: QueueEntry | None
    position: int
    is_paused: bool
    volume: int

    queue: list[QueueEntry]
    repeat_mode: RepeatMode

    filters: mafic.Filter | None

    session: MusicSession | None
