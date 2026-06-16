"""Tests for interaction response routing in feedback messages."""

import unittest
from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import discord
from discord.ui import View

from framework.feedback_ui import FeedbackType, FeedbackUI, ReportButtonView


def _await_kwargs(mock: AsyncMock) -> Mapping[str, object]:
    call = mock.await_args
    assert call is not None
    return call.kwargs


class TestFeedbackUI(unittest.IsolatedAsyncioTestCase):
    async def test_initial_response_sends_message(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()

        await FeedbackUI.send(cast(discord.Interaction, interaction), description="OK")

        interaction.response.send_message.assert_awaited_once()
        self.assertNotIn("view", _await_kwargs(interaction.response.send_message))

    async def test_completed_response_uses_followup(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = discord.InteractionResponseType.pong
        interaction.followup.send = AsyncMock(return_value=MagicMock())
        interaction.edit_original_response = AsyncMock()

        await FeedbackUI.send(cast(discord.Interaction, interaction), description="OK")

        interaction.followup.send.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        self.assertNotIn("view", _await_kwargs(interaction.followup.send))

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
        self.assertNotIn("view", _await_kwargs(interaction.edit_original_response))
        interaction.followup.send.assert_not_awaited()

    async def test_explicit_view_is_passed_to_initial_response(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        view = View()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            description="OK",
            view=view,
        )

        self.assertIs(_await_kwargs(interaction.response.send_message)["view"], view)

    async def test_none_clears_view_only_for_deferred_original_response(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = (
            discord.InteractionResponseType.deferred_channel_message
        )
        interaction.edit_original_response = AsyncMock(return_value=MagicMock())
        interaction.followup.send = AsyncMock()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            feedback_type=FeedbackType.ERROR,
            description="Error",
            view=None,
        )

        self.assertIsNone(_await_kwargs(interaction.edit_original_response)["view"])
        interaction.followup.send.assert_not_awaited()

    async def test_none_is_omitted_from_initial_response(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            feedback_type=FeedbackType.ERROR,
            description="Error",
            view=None,
        )

        self.assertNotIn("view", _await_kwargs(interaction.response.send_message))

    async def test_none_is_omitted_from_followup(self) -> None:
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.response.type = discord.InteractionResponseType.pong
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        await FeedbackUI.send(
            cast(discord.Interaction, interaction),
            feedback_type=FeedbackType.ERROR,
            description="Error",
            view=None,
        )

        self.assertNotIn("view", _await_kwargs(interaction.followup.send))

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

        view = _await_kwargs(interaction.response.send_message)["view"]
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

        embed = _await_kwargs(interaction.response.send_message)["embed"]
        assert isinstance(embed, discord.Embed)
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
