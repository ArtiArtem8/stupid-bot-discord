"""Tests for application-command error routing."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from logging import ERROR
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands

from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.exceptions import (
    BlockedUserError,
    InteractionAcknowledgementError,
)
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI


def _feedback_kwargs(send: AsyncMock) -> Mapping[str, object]:
    call = send.await_args
    if call is None:
        raise AssertionError("expected feedback to be sent")
    return call.kwargs


async def _wrap_callback_error(error: Exception) -> app_commands.CommandInvokeError:
    async def callback(_interaction: discord.Interaction) -> None:
        raise error

    command: app_commands.Command[app_commands.Group, ..., None] = app_commands.Command(
        name="failing-command",
        description="Exercise discord.py callback error wrapping",
        callback=callback,
    )
    interaction = MagicMock(spec=discord.Interaction)

    try:
        await command._do_call(interaction, {})
    except app_commands.CommandInvokeError as wrapped:
        return wrapped
    raise AssertionError("discord.py did not wrap the callback exception")


class TestCommandTreeRegistration(unittest.TestCase):
    def test_bot_uses_standard_tree_with_global_error_handler(self) -> None:
        bot = StupidBot()

        self.assertIs(type(bot.tree), app_commands.CommandTree)
        self.assertIs(bot.tree.on_error, handle_app_command_error)


class TestAppCommandErrors(unittest.IsolatedAsyncioTestCase):
    async def _assert_expected_feedback(
        self,
        error: app_commands.AppCommandError,
    ) -> Mapping[str, object]:
        interaction = MagicMock(spec=discord.Interaction)
        send = AsyncMock()

        with patch.object(FeedbackUI, "send", send):
            await handle_app_command_error(interaction, error)

        send.assert_awaited_once()
        kwargs = _feedback_kwargs(send)
        self.assertTrue(kwargs["ephemeral"])
        return kwargs

    async def test_blocked_user_gets_one_private_expected_response(self) -> None:
        kwargs = await self._assert_expected_feedback(BlockedUserError())

        self.assertIn("Доступ к командам запрещён", str(kwargs["description"]))

    async def test_direct_message_failure_gets_one_private_response(self) -> None:
        kwargs = await self._assert_expected_feedback(app_commands.NoPrivateMessage())

        self.assertIn("только на сервере", str(kwargs["description"]))

    async def test_cooldown_gets_one_private_timestamp_response(self) -> None:
        kwargs = await self._assert_expected_feedback(
            app_commands.CommandOnCooldown(
                app_commands.Cooldown(1, 10),
                retry_after=10,
            )
        )

        self.assertIn("Время ожидания:", str(kwargs["description"]))
        self.assertIn("<t:", str(kwargs["description"]))

    async def test_generic_check_failure_gets_safe_private_feedback(self) -> None:
        kwargs = await self._assert_expected_feedback(
            app_commands.CheckFailure("private check detail")
        )

        self.assertIs(kwargs["feedback_type"], FeedbackType.WARNING)
        self.assertNotIn("private check detail", str(kwargs))

    async def test_runtime_error_uses_discord_wrapping_and_safe_feedback(self) -> None:
        original = RuntimeError("private exception detail")
        error = await _wrap_callback_error(original)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.command = error.command
        interaction.guild_id = 42
        interaction.channel_id = 43
        interaction.id = 44
        send = AsyncMock()

        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR") as logs,
        ):
            await handle_app_command_error(interaction, error)

        self.assertIs(error.original, original)
        send.assert_awaited_once()
        kwargs = _feedback_kwargs(send)
        self.assertEqual(kwargs["title"], "Внутренняя ошибка")
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["error_info"], "RuntimeError")
        self.assertNotIn("private exception detail", str(kwargs))
        self.assertIn("private exception detail", "\n".join(logs.output))
        self.assertIn("guild=42 channel=43", logs.output[0])
        self.assertIn("interaction=44", logs.output[0])

    async def test_original_failure_is_logged_when_error_acknowledgement_fails(
        self,
    ) -> None:
        original = RuntimeError("storage unavailable")
        wrapped = await _wrap_callback_error(original)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.command = wrapped.command
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock(
            side_effect=discord.HTTPException(
                MagicMock(status=503, reason="Unavailable"), "lost acknowledgement"
            )
        )
        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
            self.assertLogs("stupidbot.discord_ui.errors", level="WARNING") as logs,
        ):
            await handle_app_command_error(interaction, wrapped)

        send.assert_not_awaited()
        failures = [record for record in logs.records if record.levelno == ERROR]
        self.assertEqual(len(failures), 1)
        diagnostic = failures[0]
        if not diagnostic.exc_info:
            self.fail("Missing original exception traceback")
        self.assertIs(diagnostic.exc_info[1], original)
        self.assertIsNotNone(diagnostic.exc_info[2])
        self.assertIn("Error response acknowledgement failed", logs.output[-1])

    async def test_expected_refusal_and_failed_acknowledgement_do_not_log_tracebacks(
        self,
    ) -> None:
        transport_error = discord.HTTPException(
            MagicMock(status=503, reason="Unavailable"), "private transport detail"
        )
        failed_ack = InteractionAcknowledgementError()
        failed_ack.__cause__ = transport_error
        for error in (BlockedUserError(), failed_ack):
            with self.subTest(error=type(error).__name__):
                interaction = MagicMock(spec=discord.Interaction)
                interaction.response.is_done.return_value = False
                interaction.response.defer = AsyncMock(side_effect=transport_error)
                with (
                    patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
                    self.assertLogs(
                        "stupidbot.discord_ui.errors", level="WARNING"
                    ) as logs,
                ):
                    await handle_app_command_error(interaction, error)
                send.assert_not_awaited()
                self.assertEqual(len(logs.records), 1)
                self.assertLess(logs.records[0].levelno, ERROR)
                self.assertIsNone(logs.records[0].exc_info)
                self.assertNotIn("private transport detail", logs.output[0])

    async def test_callback_discord_exception_preserves_discord_error_feedback(
        self,
    ) -> None:
        original = discord.DiscordException("private Discord detail")
        error = await _wrap_callback_error(original)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.command = error.command
        send = AsyncMock()

        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR"),
        ):
            await handle_app_command_error(interaction, error)

        send.assert_awaited_once()
        kwargs = _feedback_kwargs(send)
        self.assertEqual(kwargs["title"], "Ошибка Discord")
        self.assertEqual(kwargs["error_info"], "DiscordException")
        self.assertNotIn("private Discord detail", str(kwargs))

    async def test_unwrapped_component_discord_error_has_the_same_safe_feedback(
        self,
    ) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        error = discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "private permission detail"
        )
        send = AsyncMock()
        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR") as logs,
        ):
            await handle_app_command_error(interaction, error)

        send.assert_awaited_once()
        kwargs = _feedback_kwargs(send)
        self.assertEqual(kwargs["title"], "Ошибка Discord")
        self.assertEqual(kwargs["delete_after"], 600)
        self.assertEqual(kwargs["error_info"], "Forbidden")
        self.assertTrue(kwargs["ephemeral"])
        self.assertNotIn("private permission detail", str(kwargs))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("private permission detail", logs.output[0])

    async def test_unexpected_app_command_error_text_stays_out_of_feedback(
        self,
    ) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        send = AsyncMock()
        error = app_commands.AppCommandError("private exception detail")

        with (
            patch.object(FeedbackUI, "send", send),
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR"),
        ):
            await handle_app_command_error(interaction, error)

        send.assert_awaited_once()
        kwargs = _feedback_kwargs(send)
        self.assertEqual(kwargs["title"], "Внутренняя ошибка")
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["error_info"], "AppCommandError")
        self.assertNotIn("private exception detail", str(kwargs))
