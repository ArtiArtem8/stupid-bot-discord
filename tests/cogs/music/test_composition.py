"""Tests for the explicit music composition root."""

import unittest
from unittest.mock import MagicMock

from cogs.music.composition import create_music_components


class TestMusicComposition(unittest.TestCase):
    def test_graph_shares_each_stateful_dependency(self) -> None:
        bot = MagicMock()

        components = create_music_components(bot)

        self.assertIs(components.service.connection, components.connection)
        self.assertIs(components.service.state, components.state)
        self.assertIs(components.service.volume_repo, components.volumes)
        self.assertIs(components.service.ui, components.ui)
        self.assertIs(components.service.playback_events, components.playback_events)
        self.assertIs(components.service.voice_lifecycle, components.voice_lifecycle)
        self.assertIs(components.playback_events.connection, components.connection)
        self.assertIs(components.playback_events.state, components.state)
        self.assertIs(components.playback_events.ui, components.ui)
        self.assertIs(components.voice_lifecycle.connection, components.connection)
        self.assertIs(components.voice_lifecycle.state, components.state)
        self.assertIs(components.voice_lifecycle.ui, components.ui)
        self.assertIs(components.voice_lifecycle.healer, components.healer)
        self.assertIs(components.healer.connection, components.connection)
        self.assertIs(components.healer.state, components.state)
        self.assertIs(components.healer.ui, components.ui)
        self.assertIs(components.controllers.connection, components.connection)
        self.assertIs(components.ui.controller, components.controllers)
