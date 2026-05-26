"""Tests for user-safe music error classification."""

import unittest

from api.music.errors import MusicErrorCode, classify_music_exception
from api.music.models import NodeNotConnectedError


class TestMusicErrors(unittest.TestCase):
    def test_node_failure_is_music_node_unavailable(self) -> None:
        result = classify_music_exception(NodeNotConnectedError("secret details"))

        self.assertEqual(result.code, MusicErrorCode.MUSIC_NODE_UNAVAILABLE)
        self.assertNotIn("secret details", result.message)

    def test_unexpected_exception_is_not_exposed(self) -> None:
        result = classify_music_exception(RuntimeError("raw backend response"))

        self.assertEqual(result.code, MusicErrorCode.INTERNAL)
        self.assertNotIn("raw backend response", result.message)
