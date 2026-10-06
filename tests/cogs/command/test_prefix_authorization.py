"""Prefix hints respect blocking before command lookup and Discord requests."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands
from discord.ext import commands

from api.blocking import BlockManager
from cogs.command.no_prefix_cog import PrefixBlockerCog


class TestPrefixAuthorization(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_messages_and_empty_queries_do_not_load_block_state(
        self,
    ) -> None:
        for content in ("hello", "", "!", "! \t", "hello !play"):
            with self.subTest(content=content):
                bot = MagicMock(spec=commands.Bot)
                bot.get_prefix = AsyncMock(return_value="!")
                message = MagicMock(spec=discord.Message, content=content)
                message.author = MagicMock(spec=discord.Member, id=10, bot=False)
                message.guild = MagicMock(spec=discord.Guild, id=42)
                manager = MagicMock(spec=BlockManager)
                cog = PrefixBlockerCog(bot, manager)
                with patch.object(
                    manager, "is_user_blocked", new=AsyncMock(return_value=True)
                ) as blocked:
                    await cog.on_message(message)
                blocked.assert_not_awaited()
                bot.tree.get_commands.assert_not_called()
                bot.tree.fetch_commands.assert_not_called()
                message.reply.assert_not_awaited()

    async def test_blocked_user_does_not_trigger_prefix_hint_or_command_fetch(
        self,
    ) -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.get_prefix = AsyncMock(return_value="!")
        message = MagicMock(spec=discord.Message, content="!play")
        message.author = MagicMock(spec=discord.Member, id=10, bot=False)
        message.guild = MagicMock(spec=discord.Guild, id=42)
        manager = MagicMock(spec=BlockManager)
        cog = PrefixBlockerCog(bot, manager)
        with patch.object(
            manager, "is_user_blocked", new=AsyncMock(return_value=True)
        ) as blocked:
            await cog.on_message(message)
        blocked.assert_awaited_once_with(42, 10)
        bot.get_prefix.assert_awaited_once_with(message)
        bot.tree.get_commands.assert_not_called()
        bot.tree.fetch_commands.assert_not_called()
        message.reply.assert_not_awaited()

    async def test_unblocked_guild_and_dm_users_receive_prefix_hints(self) -> None:
        for in_guild in (True, False):
            with self.subTest(in_guild=in_guild):
                bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
                self.addAsyncCleanup(bot.close)

                async def callback(_interaction: discord.Interaction) -> None:
                    return

                bot.tree.add_command(
                    app_commands.Command(
                        name="play", description="Play", callback=callback
                    )
                )
                message = MagicMock(spec=discord.Message, content="!play")
                message.author = MagicMock(spec=discord.Member, id=10, bot=False)
                message.author.guild_permissions.administrator = False
                message.guild = (
                    MagicMock(spec=discord.Guild, id=42) if in_guild else None
                )
                manager = MagicMock(spec=BlockManager)
                cog = PrefixBlockerCog(bot, manager)
                with (
                    patch.object(
                        manager,
                        "is_user_blocked",
                        new=AsyncMock(return_value=False),
                    ) as blocked,
                    patch.object(
                        bot.tree, "fetch_commands", new=AsyncMock(return_value=[])
                    ),
                ):
                    await cog.on_message(message)
                message.reply.assert_awaited_once()
                self.assertIn("`/play`", message.reply.call_args.args[0])
                if in_guild:
                    blocked.assert_awaited_once_with(42, 10)
                else:
                    blocked.assert_not_awaited()
