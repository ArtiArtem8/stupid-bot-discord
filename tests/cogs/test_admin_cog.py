"""Tests for administration command error ownership."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.blocking_models import BlockedUser
from cogs.admin_cog import AdminCog, BlockedListPages
from framework.feedback_ui import FeedbackUI
from framework.pagination import BasePaginator


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


class TestBlockedList(unittest.IsolatedAsyncioTestCase):
    async def test_sends_blocked_members_and_departed_users_with_details(self) -> None:
        present = BlockedUser(1, "Stored name", None)
        present.add_block_entry(99, "Present reason")
        departed = BlockedUser(2, "Departed name", None)
        departed.add_block_entry(99, "Departed reason")
        unblocked = BlockedUser(3, "Unblocked name", None)
        manager = MagicMock(
            get_guild_users=AsyncMock(return_value=[present, departed, unblocked])
        )
        cog = AdminCog(MagicMock(), manager)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock(spec=discord.InteractionResponse)
        interaction.user.id = 99
        member = MagicMock(spec=discord.Member, id=1)
        member.mention = "<@1>"
        member.display_name = "Current name"
        interaction.guild.get_member.side_effect = [member, None]

        await cog.listblocked._do_call(
            interaction, {"show_details": True, "ephemeral": False}
        )

        interaction.response.send_message.assert_awaited_once()
        sent = interaction.response.send_message.await_args
        if sent is None:
            self.fail("Expected the blocked-user list")
        embed = sent.kwargs["embed"]
        self.assertEqual(embed.title, "Заблокированные пользователи (2)")
        self.assertIn("<@1> `1`", embed.description)
        self.assertIn("Current name", embed.description)
        self.assertIn("Present reason", embed.description)
        self.assertIn("Пользователь покинул сервер `2`", embed.description)
        self.assertIn("Departed name", embed.description)
        self.assertIn("Departed reason", embed.description)
        self.assertNotIn("Unblocked name", embed.description)
        self.assertFalse(sent.kwargs["ephemeral"])
        self.assertIsInstance(sent.kwargs["view"], BasePaginator)
