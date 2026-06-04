"""Tests for pure prefix suggestion selection."""

import unittest
from unittest.mock import MagicMock

from cogs.command.prefix_suggestions import find_prefix_suggestions


def _command(name: str, *, guild_only: bool = False) -> MagicMock:
    command = MagicMock()
    command.name = name
    command.guild_only = guild_only
    command.allowed_contexts = None
    return command


class TestPrefixSuggestions(unittest.TestCase):
    def test_exactish_match_returns_primary_suggestion(self) -> None:
        result = find_prefix_suggestions(
            "playy", [_command("play"), _command("pause")], in_guild=False
        )

        self.assertIsNotNone(result.primary)
        assert result.primary is not None
        self.assertEqual(result.primary.key, "play")

    def test_ambiguous_guild_match_prefers_guild_command(self) -> None:
        result = find_prefix_suggestions(
            "ban",
            [_command("banx"), _command("bany", guild_only=True)],
            in_guild=True,
        )

        self.assertIsNotNone(result.primary)
        assert result.primary is not None
        self.assertTrue(result.primary.is_guild)
        self.assertIsNotNone(result.alternative)

    def test_low_confidence_query_has_no_suggestion(self) -> None:
        result = find_prefix_suggestions(
            "unrelated", [_command("play"), _command("pause")], in_guild=False
        )

        self.assertIsNone(result.primary)
        self.assertIsNone(result.alternative)
