"""Tests for explicit playable voice connection outcomes."""

import unittest

from api.music.models import MusicResultStatus, VoiceCheckResult


class TestVoiceCheckResult(unittest.TestCase):
    def test_connected_results_are_successful(self) -> None:
        for result in (
            VoiceCheckResult.SUCCESS,
            VoiceCheckResult.ALREADY_CONNECTED,
            VoiceCheckResult.MOVED_CHANNELS,
        ):
            with self.subTest(result=result):
                self.assertIs(result.status, MusicResultStatus.SUCCESS)

    def test_failed_connection_results_do_not_allow_playback(self) -> None:
        for result in (VoiceCheckResult.TIMEOUT, VoiceCheckResult.CONNECTION_FAILED):
            with self.subTest(result=result):
                self.assertIsNot(result.status, MusicResultStatus.SUCCESS)
