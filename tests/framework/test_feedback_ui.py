"""Tests for interaction response routing in feedback messages."""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import discord

from framework.feedback_ui import FeedbackUI


class TestFeedbackUI(unittest.IsolatedAsyncioTestCase):
    async def test_deferred_channel_message_edits_original_response(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = (
            discord.InteractionResponseType.deferred_channel_message
        )
        interaction.edit_original_response = AsyncMock(return_value=MagicMock())
        interaction.followup.send = AsyncMock()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            description="Готово",
        )

        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()

    async def test_deferred_ephemeral_result_edits_original_response(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = (
            discord.InteractionResponseType.deferred_channel_message
        )
        interaction.edit_original_response = AsyncMock(return_value=MagicMock())
        interaction.followup.send = AsyncMock()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            description="Готово",
            ephemeral=True,
        )

        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
