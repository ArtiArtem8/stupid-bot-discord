"""Tests for pagination UI helpers."""

from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import MagicMock

import discord

from framework.pagination import CallbackButton


class TestCallbackButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_delegates_interaction(self) -> None:
        received: list[discord.Interaction] = []

        async def callback(interaction: discord.Interaction) -> None:
            received.append(interaction)

        button = CallbackButton[discord.ui.View](callback)
        interaction = cast(discord.Interaction, MagicMock(spec=discord.Interaction))

        await button.callback(interaction)

        self.assertEqual(received, [interaction])
