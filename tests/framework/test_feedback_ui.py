"""Tests for interaction response routing in feedback messages."""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import discord

from framework.feedback_ui import FeedbackType, FeedbackUI, ReportButtonView


class TestFeedbackUI(unittest.IsolatedAsyncioTestCase):
    async def test_initial_response_sends_message(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()

        await FeedbackUI.send(cast(discord.Interaction, interaction), description="OK")

        interaction.response.send_message.assert_awaited_once()

    async def test_completed_response_uses_followup(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = discord.InteractionResponseType.pong
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        await FeedbackUI.send(cast(discord.Interaction, interaction), description="OK")

        interaction.followup.send.assert_awaited_once()

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

    async def test_error_feedback_creates_report_button(self) -> None:
        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        FeedbackUI.configure(AsyncMock())

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            feedback_type=FeedbackType.ERROR,
            description="Error",
        )

        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertIsInstance(view, ReportButtonView)

    async def test_delete_timer_field_is_added(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            description="Temporary",
            delete_after=30,
        )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(len(embed.fields), 1)

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
