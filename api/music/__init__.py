"""Music API package."""

from .models import (
    MusicResult,
    MusicResultStatus,
    MusicSession,
    Playlist,
    QueueSnapshot,
    RepeatMode,
    Track,
    TrackGroup,
    TrackId,
    TrackInfo,
    TrackRequester,
    VoiceCheckResult,
    VoiceJoinResult,
)
from .player import MusicPlayer
from .service import CoreMusicService as MusicService

__all__ = [
    "MusicPlayer",
    "MusicResult",
    "MusicResultStatus",
    "MusicService",
    "MusicSession",
    "Playlist",
    "QueueSnapshot",
    "RepeatMode",
    "Track",
    "TrackGroup",
    "TrackId",
    "TrackInfo",
    "TrackRequester",
    "VoiceCheckResult",
    "VoiceJoinResult",
]
