"""Error policy and bounded external diagnostics for music operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import aiohttp
import mafic

from utils.text_utils import truncate_text

from .models import MUSIC_SERVICE_UNAVAILABLE_MESSAGE, NodeNotConnectedError

EXTERNAL_LOG_TEXT_LIMIT = 320
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


def compact_external_log_text(
    value: object | None,
    *,
    limit: int = EXTERNAL_LOG_TEXT_LIMIT,
) -> str | None:
    """Normalize external failure text for one-line operational logs.

    Whitespace is collapsed before truncation so the useful beginning of a
    multiline vendor diagnostic remains searchable without expanding the log
    record into a stack trace.
    """
    if value is None:
        return None

    text = " ".join(str(value).split())
    return truncate_text(text, width=limit, placeholder="…")


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
    if isinstance(exc, (*NODE_TRANSPORT_ERRORS, TimeoutError, NodeNotConnectedError)):
        return UserFacingMusicError(
            MusicErrorCode.MUSIC_NODE_UNAVAILABLE,
            MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
        )
    return UserFacingMusicError(
        MusicErrorCode.INTERNAL,
        "Внутренняя ошибка музыкального модуля. Детали записаны в лог.",
    )
