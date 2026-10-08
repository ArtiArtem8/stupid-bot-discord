"""Tests for administration command error ownership."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.admin_cog import AdminCog, BlockedListPages
from framework.feedback_ui import FeedbackUI


class TestDeleteMessage(unittest.IsolatedAsyncioTestCase):
    def _make_context(self) -> tuple[AdminCog, MagicMock, MagicMock]:
        cog = AdminCog(MagicMock(), MagicMock())
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
            self.assertRaises(RuntimeError) as raised,
        ):
            await cast(Any, AdminCog.delete_message).callback(cog, interaction, "123")

        self.assertIs(raised.exception, error)
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
            await cast(Any, AdminCog.delete_message).callback(cog, interaction, "123")

        feedback.assert_awaited_once()
        call = feedback.await_args
        if call is None:
            self.fail("expected feedback to be sent")
        self.assertEqual(call.kwargs["description"], "Нет прав.")
        self.assertTrue(call.kwargs["ephemeral"])


class TestBlockedPages(unittest.TestCase):
    def test_large_list_retains_every_entry_in_bounded_messages(self) -> None:
        entries = [f"USER-{index}: " + "x" * 450 for index in range(100)]
        pages = BlockedListPages(entries, show_details=True)
        self.assertGreater(len(pages.pages), 1)
        self.assertEqual("\n".join(pages.pages), "\n".join(entries))
        for index in range(len(pages.pages)):
            embed = pages.make_embed(index)
            self.assertLessEqual(len(embed), 6000)
            self.assertLessEqual(len(embed.description or ""), 4096)
