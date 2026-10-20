"""Tests for pagination UI helpers."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from stupidbot.discord_ui.pagination import BasePaginator, CallbackButton, ManagedView


class TestManagedView(unittest.IsolatedAsyncioTestCase):
    async def test_deferred_command_edits_and_retains_original_for_timeout(
        self,
    ) -> None:
        view = ManagedView()
        self.addCleanup(view.stop)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock(spec=discord.InteractionResponse)
        interaction.response.type = (
            discord.InteractionResponseType.deferred_channel_message
        )
        message = MagicMock(spec=discord.InteractionMessage)
        interaction.edit_original_response = AsyncMock(return_value=message)
        embed = discord.Embed(description="A page")
        await view.send(interaction, embed=embed, ephemeral=True, silent=True)
        interaction.response.send_message.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once_with(
            embed=embed, view=view
        )
        await view.on_timeout()
        message.edit.assert_awaited_once_with(view=None)


class TestCallbackButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_delegates_interaction(self) -> None:
        received: list[discord.Interaction] = []

        async def callback(interaction: discord.Interaction) -> None:
            received.append(interaction)

        button = CallbackButton[discord.ui.View](callback)
        interaction = MagicMock(spec=discord.Interaction)

        await button.callback(interaction)

        self.assertEqual(received, [interaction])


class TestPaginatorNavigation(unittest.IsolatedAsyncioTestCase):
    async def test_stale_forward_clicks_acknowledge_and_reset_to_the_remaining_page(
        self,
    ) -> None:
        for last in (False, True):
            with self.subTest(last=last):
                data = MagicMock(get_page_count=AsyncMock(return_value=1))
                data.make_embed.return_value = discord.Embed(description="remaining")
                view = BasePaginator(data, user_id=42)
                view.page = 3
                interaction = MagicMock(spec=discord.Interaction)
                interaction.response = MagicMock(edit_message=AsyncMock())

                if last:
                    await view.last_page(interaction)
                else:
                    await view.next_page(interaction)

                interaction.response.edit_message.assert_awaited_once_with(
                    embed=data.make_embed.return_value, view=view
                )
                self.assertEqual(view.page, 0)
                data.make_embed.assert_called_once_with(0)
                self.assertTrue(view.next_btn.disabled)
                self.assertTrue(view.prev_btn.disabled)
                view.stop()

    async def test_navigation_uses_one_page_count_snapshot(self) -> None:
        for last, expected in ((False, 1), (True, 3)):
            with self.subTest(last=last):
                data = MagicMock(get_page_count=AsyncMock(side_effect=[4, 1]))
                view = BasePaginator(data, user_id=42)
                interaction = MagicMock(spec=discord.Interaction)
                interaction.response = MagicMock(edit_message=AsyncMock())

                if last:
                    await view.last_page(interaction)
                else:
                    await view.next_page(interaction)

                data.get_page_count.assert_awaited_once()
                data.make_embed.assert_called_once_with(expected)
                self.assertEqual(view.page, expected)
                view.stop()


class TestPaginatorClose(unittest.IsolatedAsyncioTestCase):
    async def test_close_stops_listening_before_discord_responds(self) -> None:
        view = BasePaginator(MagicMock(), user_id=42)
        self.addCleanup(view.stop)
        interaction = MagicMock(spec=discord.Interaction)
        response_started = asyncio.Event()
        release_response = asyncio.Event()

        async def respond(**_kwargs: object) -> None:
            response_started.set()
            await release_response.wait()

        interaction.response = MagicMock(edit_message=AsyncMock(side_effect=respond))
        task = asyncio.create_task(view.close(interaction))
        try:
            await response_started.wait()
            self.assertTrue(view.is_finished())
        finally:
            release_response.set()
            await task
        interaction.response.edit_message.assert_awaited_once_with(view=None)

    async def test_close_remains_stopped_after_failure_or_cancellation(self) -> None:
        response = MagicMock(status=503, reason="Service unavailable")
        for error in (
            discord.HTTPException(response, "test failure"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                view = BasePaginator(MagicMock(), user_id=42)
                self.addCleanup(view.stop)
                interaction = MagicMock(spec=discord.Interaction)
                interaction.response = MagicMock(
                    edit_message=AsyncMock(side_effect=error)
                )

                with self.assertRaises(type(error)):
                    await view.close(interaction)

                self.assertTrue(view.is_finished())

    async def test_close_accepts_an_already_deleted_message(self) -> None:
        view = BasePaginator(MagicMock(), user_id=42)
        self.addCleanup(view.stop)
        interaction = MagicMock(spec=discord.Interaction)
        response = MagicMock(status=404, reason="Not found")
        interaction.response = MagicMock(
            edit_message=AsyncMock(side_effect=discord.NotFound(response, "deleted"))
        )

        await view.close(interaction)

        self.assertTrue(view.is_finished())
