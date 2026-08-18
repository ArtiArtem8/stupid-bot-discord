"""Tests for safe command-error feedback."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

from framework import FeedbackType, FeedbackUI, handle_errors
from framework.error_handler import CustomErrorCommandTree


class TestCommandTreeErrors(unittest.IsolatedAsyncioTestCase):
    async def test_generic_check_failure_gets_private_feedback(self) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        send = AsyncMock()
        tree = MagicMock(spec=CustomErrorCommandTree)

        with patch.object(FeedbackUI, "send", send):
            await CustomErrorCommandTree.on_error(
                tree,
                interaction,
                app_commands.CheckFailure("private check detail"),
            )

        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        self.assertIs(kwargs["feedback_type"], FeedbackType.WARNING)
        self.assertTrue(kwargs["ephemeral"])
        self.assertNotIn("private check detail", str(kwargs))

    async def test_unexpected_error_details_stay_out_of_feedback(self) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        send = AsyncMock()
        tree = MagicMock(spec=CustomErrorCommandTree)
        error = app_commands.AppCommandError("private exception detail")

        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("framework.error_handler", level="ERROR"),
        ):
            await CustomErrorCommandTree.on_error(tree, interaction, error)

        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["title"], "Внутренняя ошибка")
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["error_info"], "AppCommandError")
        self.assertNotIn("private exception detail", str(kwargs))


class TestHandleErrors(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_error_details_stay_out_of_feedback(self) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        send = AsyncMock()

        class TestCog:
            @handle_errors()
            async def command(self, _interaction: discord.Interaction) -> None:
                raise RuntimeError("private exception detail")

        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("framework.decorators", level="ERROR"),
        ):
            await TestCog().command(interaction)

        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["error_info"], "RuntimeError")
        self.assertNotIn("private exception detail", str(kwargs))
