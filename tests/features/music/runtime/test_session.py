"""Tests for music session lifecycle event helpers."""

import unittest
from unittest.mock import MagicMock

from stupidbot.features.music.runtime.session import (
    MusicSession,
    dispatch_music_session_end,
    main_session_channel_id,
)


class TestMusicSessionEvents(unittest.TestCase):
    def test_main_session_channel_id_returns_most_used_channel(self) -> None:
        session = MusicSession(guild_id=1, channel_usage={10: 2, 20: 5})

        self.assertEqual(main_session_channel_id(session), 20)

    def test_dispatch_music_session_end_dispatches_reportable_session(self) -> None:
        bot = MagicMock()
        session = MusicSession(guild_id=1, channel_usage={10: 2})
        session.tracks.append(MagicMock())

        dispatch_music_session_end(bot, 1, session)

        bot.dispatch.assert_called_once_with("music_session_end", 1, session, 10)

    def test_dispatch_music_session_end_ignores_empty_session(self) -> None:
        bot = MagicMock()

        dispatch_music_session_end(bot, 1, MusicSession(guild_id=1))

        bot.dispatch.assert_not_called()


class TestMusicSession(unittest.TestCase):
    def test_track_without_requester_does_not_add_participant_zero(self) -> None:
        session = MusicSession(guild_id=1)

        session.add_track(
            title="Track",
            uri="https://example.com/track",
            requester_id=None,
            channel_id=None,
        )

        self.assertEqual(session.participants, set())
