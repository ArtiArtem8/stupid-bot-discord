"""Environment overrides keep old installs usable and new installs portable."""

import os
import unittest
from unittest.mock import patch

from stupidbot.discord_ui.emojis import _emoji
from stupidbot.settings import discord_emoji_id


class TestEmojiConfiguration(unittest.TestCase):
    def test_absent_override_keeps_original_custom_emoji(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_emoji("play", 123, "▶️"), "<:play:123>")

    def test_explicit_empty_override_uses_unicode(self) -> None:
        with patch.dict(os.environ, {"DISCORD_EMOJI_PLAY_ID": "  "}):
            self.assertEqual(_emoji("play", 123, "▶️"), "▶️")

    def test_override_uses_uploaded_emoji_id(self) -> None:
        with patch.dict(os.environ, {"DISCORD_EMOJI_PLAY_ID": "456"}):
            self.assertEqual(_emoji("play", 123, "▶️"), "<:play:456>")

    def test_invalid_id_identifies_setting_without_echoing_value(self) -> None:
        for value in ("zero", "0", "-1", "1.5", "１２３", str(2**64)):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"DISCORD_EMOJI_PLAY_ID": value}),
                self.assertRaisesRegex(ValueError, "^DISCORD_EMOJI_PLAY_ID must be"),
            ):
                discord_emoji_id("play", 123)
