"""Music API package."""

from .models import (
    ControllerDestroyReason,
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
from .protocols import ControllerManagerProtocol, HealerProtocol
from .service import CoreMusicService as MusicService

__all__ = [
    "ControllerDestroyReason",
    "ControllerManagerProtocol",
    "HealerProtocol",
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
