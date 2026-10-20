"""Tests for interaction acknowledgement flow helpers."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from stupidbot.discord_ui.interaction_flow import ack_component


def _make_interaction(*, responded: bool = False) -> MagicMock:
    response = MagicMock(spec=discord.InteractionResponse)
    response.is_done.return_value = responded
    response.defer = AsyncMock()

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = response
    return interaction


class TestAckComponent(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_without_spinner(self) -> None:
        interaction = _make_interaction()

        await ack_component(interaction)

        interaction.response.defer.assert_awaited_once_with(
            thinking=False,
            ephemeral=False,
        )

    async def test_already_responded_is_noop(self) -> None:
        interaction = _make_interaction(responded=True)

        await ack_component(interaction)

        interaction.response.defer.assert_not_awaited()
