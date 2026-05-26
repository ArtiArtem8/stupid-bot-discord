"""User-facing error policy for music operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import mafic

from .models import NodeNotConnectedError


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
    if isinstance(exc, NodeNotConnectedError):
        return UserFacingMusicError(
            MusicErrorCode.MUSIC_NODE_UNAVAILABLE,
            "Музыкальный сервер сейчас недоступен. Попробуйте позже.",
        )
    if isinstance(exc, (mafic.PlayerNotConnected, mafic.PlayerException)):
        return UserFacingMusicError(
            MusicErrorCode.PLAYER_DISCONNECTED,
            "Плеер потерял соединение. Попробуйте запустить трек ещё раз.",
        )
    return UserFacingMusicError(
        MusicErrorCode.INTERNAL,
        "Внутренняя ошибка музыкального модуля. Детали записаны в лог.",
    )
