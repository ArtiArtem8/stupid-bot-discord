from __future__ import annotations

import asyncio
import io
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
from discord.ext import commands

import config
from api.wolfram import (
    WolframAPIError,
    WolframClient,
    WolframRateLimitError,
    WolframResult,
)
from cogs.wolfram_cog import WolframCog, _normalize_query
from framework import FeedbackUI
from utils import ImageOutputTooLargeError


class TestWolframCog(unittest.IsolatedAsyncioTestCase):
    def _make_cog(self) -> tuple[WolframCog, MagicMock]:
        cog = object.__new__(WolframCog)
        client = MagicMock(spec=WolframClient)
        client.fetch_plot_image.return_value = b"source"
        cog.wolfram_client = client
        cog.client_session = MagicMock(spec=aiohttp.ClientSession)
        cog._request_semaphore = asyncio.Semaphore(2)
        cog.bot = MagicMock(spec=commands.Bot)
        return cog, client

    def _make_interaction(
        self,
        *,
        guild_limit: int | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        interaction = MagicMock(spec=discord.Interaction)
        channel = MagicMock(spec=discord.abc.Messageable)
        message = MagicMock(spec=discord.Message)
        message.jump_url = "https://discord.invalid/messages/1"
        channel.send.return_value = message
        interaction.channel = channel
        interaction.user.mention = "@plotter"
        interaction.user.id = 1234
        interaction.guild_id = 5678
        interaction.filesize_limit = (
            guild_limit
            if guild_limit is not None
            else config.WOLFRAM_PLOT_MAX_UPLOAD_BYTES
        )
        interaction.guild = (
            MagicMock(filesize_limit=guild_limit) if guild_limit is not None else None
        )
        return interaction, channel

    def test_normalize_query_strips_and_rejects_invalid_input(self) -> None:
        self.assertEqual(_normalize_query("  sin(x)  "), "sin(x)")
        for query in ("", "   ", "x" * (config.WOLFRAM_MAX_QUERY_LEN + 1)):
            with self.subTest(query_length=len(query)):
                with self.assertRaises(ValueError):
                    _normalize_query(query)

    def test_slash_parameters_enforce_query_length_range(self) -> None:
        solve_parameter = WolframCog.cmd_solve.parameters[0]
        plot_parameter = WolframCog.cmd_plot.parameters[0]

        for parameter in (solve_parameter, plot_parameter):
            with self.subTest(parameter=parameter.name):
                self.assertEqual(parameter.min_value, 1)
                self.assertEqual(parameter.max_value, config.WOLFRAM_MAX_QUERY_LEN)

    async def test_commands_share_one_per_user_cooldown(self) -> None:
        solve_check = WolframCog.cmd_solve.checks[0]
        plot_check = WolframCog.cmd_plot.checks[0]
        context_checks: Any = getattr(
            WolframCog._context_solve,
            "__discord_app_commands_checks__",
            (),
        )
        context_check = context_checks[0]
        self.assertIs(solve_check, plot_check)
        self.assertIs(solve_check, context_check)

        first, _ = self._make_interaction()
        second, _ = self._make_interaction()
        now = datetime(2026, 7, 18, tzinfo=UTC)
        first.created_at = now
        second.created_at = now + timedelta(seconds=1)

        self.assertTrue(await discord.utils.maybe_coroutine(solve_check, first))
        with self.assertRaises(discord.app_commands.CommandOnCooldown):
            await discord.utils.maybe_coroutine(plot_check, second)

    async def test_handle_query_normalizes_before_api_request(self) -> None:
        cog, client = self._make_cog()
        interaction, _ = self._make_interaction()
        client.query.return_value = WolframResult(success=False)

        with patch.object(FeedbackUI, "send", new=AsyncMock()):
            await cog._handle_query(interaction, "  sin(x)  ", mode="solve")

        client.query.assert_awaited_once_with("solve sin(x)")

    async def test_handle_query_rejects_whitespace_before_api_request(self) -> None:
        cog, client = self._make_cog()
        interaction, _ = self._make_interaction()

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback:
            await cog._handle_query(interaction, "   ", mode="solve")

        client.query.assert_not_awaited()
        self.assertEqual(
            feedback.await_args_list[-1].kwargs["description"],
            "Query empty or too long.",
        )

    async def test_wolfram_processing_is_limited_to_two_concurrent_requests(
        self,
    ) -> None:
        cog, client = self._make_cog()
        active = 0
        peak = 0

        async def query(_input: str) -> WolframResult:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return WolframResult(success=False)

        client.query.side_effect = query
        interactions = [self._make_interaction()[0] for _ in range(3)]
        with patch.object(FeedbackUI, "send", new=AsyncMock()):
            await asyncio.gather(
                *(
                    cog._handle_query(item, "sin(x)", mode="solve")
                    for item in interactions
                )
            )

        self.assertEqual(peak, 2)

    async def test_invalid_channel_is_rejected_before_http(self) -> None:
        cog, client = self._make_cog()
        interaction, _ = self._make_interaction()
        interaction.channel = None

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
            patch("cogs.wolfram_cog.asyncio.to_thread", new=AsyncMock()) as to_thread,
        ):
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        client.fetch_plot_image.assert_not_awaited()
        to_thread.assert_not_awaited()
        self.assertEqual(
            feedback.await_args_list[-1].kwargs["description"],
            "Cannot upload images in this context.",
        )

    async def test_download_processing_and_file_upload(self) -> None:
        cog, client = self._make_cog()
        interaction, channel = self._make_interaction()
        file_seen: discord.File | None = None
        buffer_open_during_send = False

        async def send(**kwargs: Any) -> MagicMock:
            nonlocal file_seen, buffer_open_during_send
            file_seen = kwargs["file"]
            buffer_open_during_send = not file_seen.fp.closed
            self.assertIsInstance(file_seen.fp, io.BytesIO)
            self.assertEqual(file_seen.fp.read(), b"webp")
            message = MagicMock(spec=discord.Message)
            message.jump_url = "https://discord.invalid/messages/1"
            return message

        channel.send.side_effect = send

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
            patch(
                "cogs.wolfram_cog.asyncio.to_thread",
                new=AsyncMock(return_value=b"webp"),
            ) as to_thread,
        ):
            await cog._send_plot(
                interaction,
                "https://example.invalid/plot",
                "sin(x)",
                result_query="plot sin(x)",
            )

        client.fetch_plot_image.assert_awaited_once_with(
            "https://example.invalid/plot",
            max_bytes=config.WOLFRAM_PLOT_MAX_DOWNLOAD_BYTES,
        )
        self.assertEqual(
            to_thread.await_args_list[-1].args[0].__name__,
            "process_wolfram_plot",
        )
        self.assertEqual(file_seen.filename if file_seen else None, "wolfram_plot.webp")
        self.assertTrue(buffer_open_during_send)
        self.assertTrue(file_seen.fp.closed if file_seen else False)
        self.assertEqual(
            channel.send.await_args_list[-1].kwargs["content"],
            "@plotter **Plot:** `sin(x)`\n"
            + "**[View on Wolfram|Alpha](https://www.wolframalpha.com/"
            + "input?i=plot+sin%28x%29)**",
        )
        self.assertEqual(
            feedback.await_args_list[-1].kwargs["description"],
            "Graph generated: https://discord.invalid/messages/1",
        )

    async def test_smaller_guild_limit_becomes_output_budget(self) -> None:
        cog, _ = self._make_cog()
        interaction, _ = self._make_interaction(guild_limit=1_000_000)

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()),
            patch(
                "cogs.wolfram_cog.asyncio.to_thread",
                new=AsyncMock(return_value=b"webp"),
            ) as to_thread,
        ):
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        self.assertEqual(
            to_thread.await_args_list[-1].kwargs["max_output_bytes"], 1_000_000
        )

    async def test_global_limit_caps_larger_guild_limit(self) -> None:
        cog, _ = self._make_cog()
        interaction, _ = self._make_interaction(guild_limit=50_000_000)

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()),
            patch(
                "cogs.wolfram_cog.asyncio.to_thread",
                new=AsyncMock(return_value=b"webp"),
            ) as to_thread,
        ):
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        self.assertEqual(
            to_thread.await_args_list[-1].kwargs["max_output_bytes"],
            config.WOLFRAM_PLOT_MAX_UPLOAD_BYTES,
        )

    async def test_oversized_output_is_not_sent(self) -> None:
        cog, _ = self._make_cog()
        interaction, channel = self._make_interaction()

        with (
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
            patch(
                "cogs.wolfram_cog.asyncio.to_thread",
                new=AsyncMock(side_effect=ImageOutputTooLargeError("too large")),
            ),
        ):
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        channel.send.assert_not_awaited()
        self.assertEqual(feedback.await_args_list[-1].kwargs["title"], "Image Error")
        self.assertEqual(
            feedback.await_args_list[-1].kwargs["description"],
            "Failed to process graph image.",
        )

    async def test_download_error_preserves_image_error_feedback(self) -> None:
        cog, client = self._make_cog()
        interaction, channel = self._make_interaction()
        client.fetch_plot_image.side_effect = WolframAPIError("failed")

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback:
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        channel.send.assert_not_awaited()
        self.assertEqual(feedback.await_args_list[-1].kwargs["title"], "Image Error")

    async def test_download_429_uses_specific_api_feedback(self) -> None:
        cog, client = self._make_cog()
        interaction, channel = self._make_interaction()
        client.fetch_plot_image.side_effect = WolframRateLimitError(
            "Wolfram is busy. Please try again in a few seconds."
        )

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback:
            await cog._send_plot(interaction, "https://example.invalid/plot", "sin(x)")

        channel.send.assert_not_awaited()
        self.assertEqual(feedback.await_args_list[-1].kwargs["title"], "API Error")
        self.assertIn("try again", feedback.await_args_list[-1].kwargs["description"])

    async def test_cancellation_propagates_from_download(self) -> None:
        cog, client = self._make_cog()
        interaction, _ = self._make_interaction()
        client.fetch_plot_image.side_effect = asyncio.CancelledError()

        with patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback:
            with self.assertRaises(asyncio.CancelledError):
                await cog._send_plot(
                    interaction, "https://example.invalid/plot", "sin(x)"
                )

        feedback.assert_not_awaited()

    async def test_cog_load_creates_one_client_for_persistent_session(self) -> None:
        cog = object.__new__(WolframCog)
        cog.client_session = None
        cog.wolfram_client = None
        cog._request_semaphore = asyncio.Semaphore(2)
        session = MagicMock(spec=aiohttp.ClientSession)
        client = MagicMock(spec=WolframClient)

        with (
            patch("cogs.wolfram_cog.aiohttp.ClientSession", return_value=session),
            patch("cogs.wolfram_cog.WolframClient", return_value=client) as client_type,
            patch.object(config, "WOLFRAM_APP_ID", "test-app-id"),
        ):
            await cog.cog_load()

        self.assertIs(cog.client_session, session)
        self.assertIs(cog.wolfram_client, client)
        client_type.assert_called_once_with("test-app-id", session=session)

    async def test_session_is_closed_in_cog_unload(self) -> None:
        cog, _ = self._make_cog()
        session = MagicMock()
        close = AsyncMock()
        session.close = close
        cog.client_session = session
        remove_command = MagicMock()
        cog.bot.tree.remove_command = remove_command
        cog.ctx_menu = MagicMock()
        cog.ctx_menu.name = "Solve with Wolfram"
        cog.ctx_menu.type = discord.AppCommandType.message

        await cog.cog_unload()

        close.assert_awaited_once()
        remove_command.assert_called_once_with(
            "Solve with Wolfram", type=discord.AppCommandType.message
        )
        self.assertIsNone(cog.client_session)
        self.assertIsNone(cog.wolfram_client)
