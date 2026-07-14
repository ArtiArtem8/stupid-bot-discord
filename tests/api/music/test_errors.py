"""Tests for user-safe music error classification."""

import unittest

import aiohttp
import mafic

from api.music.errors import MusicErrorCode, classify_music_exception
from api.music.models import MUSIC_SERVICE_UNAVAILABLE_MESSAGE, NodeNotConnectedError


class TestMusicErrors(unittest.TestCase):
    def test_node_failure_is_music_node_unavailable(self) -> None:
        result = classify_music_exception(NodeNotConnectedError("secret details"))

        self.assertEqual(result.code, MusicErrorCode.MUSIC_NODE_UNAVAILABLE)
        self.assertNotIn("secret details", result.message)

    def test_unexpected_exception_is_not_exposed(self) -> None:
        result = classify_music_exception(RuntimeError("raw backend response"))

        self.assertEqual(result.code, MusicErrorCode.INTERNAL)
        self.assertNotIn("raw backend response", result.message)

    def test_player_lifecycle_failures_use_disconnected_message(self) -> None:
        expected_message = (
            "Плеер потерял соединение. Попробуйте запустить трек ещё раз."
        )

        for error in (mafic.PlayerNotConnected(), mafic.HTTPNotFound("missing")):
            with self.subTest(error=type(error).__name__):
                result = classify_music_exception(error)

                self.assertEqual(result.code, MusicErrorCode.PLAYER_DISCONNECTED)
                self.assertEqual(result.message, expected_message)

    def test_track_load_failure_uses_source_unavailable_message(self) -> None:
        error = mafic.TrackLoadException(
            message="load failed",
            severity="COMMON",
            cause="backend detail",
        )

        result = classify_music_exception(error)

        self.assertEqual(result.code, MusicErrorCode.SOURCE_UNAVAILABLE)
        self.assertEqual(
            result.message,
            "Не удалось загрузить трек. Источник временно недоступен или не ответил.",
        )

    def test_node_transport_failures_use_service_unavailable_message(self) -> None:
        for error in (
            aiohttp.ClientConnectionError("down"),
            TimeoutError("timed out"),
        ):
            with self.subTest(error=type(error).__name__):
                result = classify_music_exception(error)

                self.assertEqual(
                    result.code,
                    MusicErrorCode.MUSIC_NODE_UNAVAILABLE,
                )
                self.assertEqual(result.message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)
