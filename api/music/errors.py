"""User-facing error policy for music operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import aiohttp
import mafic

from .models import MUSIC_SERVICE_UNAVAILABLE_MESSAGE, NodeNotConnectedError

NODE_TRANSPORT_ERRORS = (aiohttp.ClientError,)

PLAYER_LIFECYCLE_ERRORS = (
    mafic.HTTPNotFound,
    mafic.PlayerNotConnected,
)

EXPECTED_LAVALINK_IO_ERRORS = (
    *NODE_TRANSPORT_ERRORS,
    TimeoutError,
    *PLAYER_LIFECYCLE_ERRORS,
    mafic.PlayerException,
)


def is_player_lifecycle_error(exc: Exception) -> bool:
    """Return whether an error means the player lifecycle is no longer usable."""
    return isinstance(exc, PLAYER_LIFECYCLE_ERRORS) or (
        isinstance(exc, mafic.PlayerException)
        and not isinstance(exc, mafic.TrackLoadException)
    )


class MusicErrorCode(StrEnum):
    """Stable categories for errors exposed by the music module."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    MUSIC_NODE_UNAVAILABLE = "music_node_unavailable"
    PLAYER_DISCONNECTED = "player_disconnected"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class UserFacingMusicError:
    """Safe error information that may be displayed to a user."""

    code: MusicErrorCode
    message: str


def classify_music_exception(exc: Exception) -> UserFacingMusicError:
    """Map implementation exceptions to short and stable user messages."""
    if isinstance(exc, mafic.TrackLoadException):
        return UserFacingMusicError(
            MusicErrorCode.SOURCE_UNAVAILABLE,
            "Не удалось загрузить трек. Источник временно недоступен или не ответил.",
        )
    if is_player_lifecycle_error(exc):
        return UserFacingMusicError(
            MusicErrorCode.PLAYER_DISCONNECTED,
            "Плеер потерял соединение. Попробуйте запустить трек ещё раз.",
        )
    if isinstance(exc, NODE_TRANSPORT_ERRORS) or isinstance(
        exc, (TimeoutError, NodeNotConnectedError)
    ):
        return UserFacingMusicError(
            MusicErrorCode.MUSIC_NODE_UNAVAILABLE,
            MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
        )
    return UserFacingMusicError(
        MusicErrorCode.INTERNAL,
        "Внутренняя ошибка музыкального модуля. Детали записаны в лог.",
    )
