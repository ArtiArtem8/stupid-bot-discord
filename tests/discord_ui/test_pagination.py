"""Tests for pagination UI helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from stupidbot.discord_ui.pagination import BasePaginator, CallbackButton


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
