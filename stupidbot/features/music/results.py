"""Typed outcomes of music and voice connection operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Literal, NotRequired, TypedDict

import discord

from stupidbot.features.music.playback.models import (
    Playlist,
    PlayPlacement,
    QueueEntry,
    Track,
)

MUSIC_SERVICE_UNAVAILABLE_MESSAGE = (
    "Музыкальный сервис сейчас недоступен. Попробуйте позже."
)
MUSIC_RECOVERING_MESSAGE = (
    "Восстанавливаю соединение. Попробуй ещё раз после завершения."
)


class PlaylistResponseData(TypedDict):
    """Playlist result and the queue entries eligible for undo."""

    type: Literal["playlist"]
    playlist: Playlist
    undo_entries: tuple[QueueEntry, ...]
    placement: PlayPlacement
    connection: NotRequired[VoiceJoinResult]


class TrackResponseData(TypedDict):
    """Track result and the queue entries eligible for undo."""

    type: Literal["track"]
    track: Track
    undo_entries: tuple[QueueEntry, ...]
    placement: PlayPlacement
    connection: NotRequired[VoiceJoinResult]


type PlayResponseData = PlaylistResponseData | TrackResponseData


class SkipTrackData(TypedDict):
    """Tracks before and after a skip."""

    before: Track | None
    after: Track | None


class RepeatModeData(TypedDict):
    """Data for repeat mode change."""

    mode: str
    previous: str


class RotateTrackData(TypedDict):
    """Tracks before and after moving the current track to the queue's end."""

    skipped: Track | None
    next: Track | None


class MusicErrorCode(StrEnum):
    """Stable categories for errors exposed by the music module."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    MUSIC_NODE_UNAVAILABLE = "music_node_unavailable"
    PLAYER_DISCONNECTED = "player_disconnected"
    INTERNAL = "internal"


class MusicResultStatus(StrEnum):
    """Status of a music operation."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ERROR = "ERROR"


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
    RECOVERING = auto()
    OTHER_CHANNEL = auto()

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


@dataclass(frozen=True, slots=True)
class MusicResult[T]:
    """Operation result whose reason, independent of display text, drives policy."""

    status: MusicResultStatus
    message: str
    data: T | None = None
    reason: MusicResultReason | MusicErrorCode = field(kw_only=True)

    @property
    def is_success(self) -> bool:
        return self.status is MusicResultStatus.SUCCESS


class MusicResultReason(StrEnum):
    """Stable operation outcomes; transport failures use MusicErrorCode."""

    NOT_CONNECTED = "not_connected"
    DISCONNECTED = "disconnected"
    DISCONNECT_FAILED = "disconnect_failed"
    PLAYER_LOST = "player_lost"
    PLAYER_CHANGED = "player_changed"
    CONNECTION_FAILED = "connection_failed"
    NOTHING_FOUND = "nothing_found"
    PLAYLIST_ADDED = "playlist_added"
    TRACK_PROCESSED = "track_processed"
    RECOVERING = "recovering"
    STALE_QUEUE_ENTRIES = "stale_queue_entries"
    REMOVED_FROM_QUEUE = "removed_from_queue"
    STOPPED = "stopped"
    SKIPPED = "skipped"
    PAUSED = "paused"
    RESUMED = "resumed"
    SHUFFLED = "shuffled"
    NOTHING_PLAYING = "nothing_playing"
    ROTATED = "rotated"
    VOLUME_SET = "volume_set"
    REPEAT_UPDATED = "repeat_updated"
    QUEUE_EMPTY = "queue_empty"
    QUEUE_RETRIEVED = "queue_retrieved"
    NO_PLAYER = "no_player"
