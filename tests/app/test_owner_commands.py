"""Tests for administration command error ownership."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.app import owner_cog
from stupidbot.app.owner_cog import OwnerCog
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.i18n.policy import Language, PresentationContext


class TestDeleteMessage(unittest.IsolatedAsyncioTestCase):
    def _make_context(self) -> tuple[OwnerCog, MagicMock, MagicMock]:
        cog = OwnerCog(MagicMock())
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock(spec=discord.InteractionResponse)
        channel = MagicMock(spec=discord.TextChannel)
        channel.fetch_message = AsyncMock()
        interaction.channel = channel
        return cog, interaction, channel

    async def test_unexpected_fetch_failure_propagates_to_global_boundary(self) -> None:
        cog, interaction, channel = self._make_context()
        error = RuntimeError("programming failure")
        channel.fetch_message.side_effect = error

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
            self.assertRaises(discord.app_commands.CommandInvokeError) as raised,
        ):
            await cog.delete_message._do_call(interaction, {"message_id": "123"})

        self.assertIs(raised.exception.original, error)
        feedback.assert_not_awaited()

    async def test_forbidden_delete_gets_one_expected_response(self) -> None:
        cog, interaction, channel = self._make_context()
        response = MagicMock(status=403, reason="Forbidden")
        message = MagicMock()
        message.delete = AsyncMock(
            side_effect=discord.Forbidden(
                response,
                {"code": 50013, "message": "Missing Permissions"},
            )
        )
        channel.fetch_message.return_value = message

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback:
            await cog.delete_message._do_call(interaction, {"message_id": "123"})

        feedback.assert_awaited_once()
        call = feedback.await_args
        if call is None:
            self.fail("expected feedback to be sent")
        self.assertEqual(call.kwargs["description"], "Нет прав.")
        self.assertTrue(call.kwargs["ephemeral"])

    async def test_preferences_are_read_after_acknowledgement(self) -> None:
        cog, interaction, channel = self._make_context()
        channel.fetch_message.return_value = MagicMock(delete=AsyncMock())

        async def resolve(
            request: discord.Interaction, *, private: bool
        ) -> PresentationContext:
            self.assertIs(request, interaction)
            self.assertTrue(private)
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            return PresentationContext(Language.RU, ZoneInfo("UTC"))

        with (
            patch.object(owner_cog, "interaction_context", side_effect=resolve),
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
        ):
            await cog.delete_message._do_call(interaction, {"message_id": "123"})

        channel.fetch_message.assert_awaited_once_with(123)
        feedback.assert_awaited_once()
