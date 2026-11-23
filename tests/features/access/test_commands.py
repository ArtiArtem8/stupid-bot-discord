"""Tests for Discord administration commands and their feedback."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord

from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.pagination import BasePaginator
from stupidbot.features.access.access_cog import AdminCog
from stupidbot.features.access.models import BlockedUser, BlockHistoryEntry
from stupidbot.features.access.presentation import BlockedListPages, format_blocked_user
from stupidbot.i18n.policy import Language
from tests.storage.fixtures import temporary_database


class TestBlockCommandDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_registered_block_and_unblock_commit_state_and_audit(self) -> None:
        path, database = await temporary_database(self)
        await database.close()
        async with StupidBot(database_path=path) as bot:
            cog = AdminCog(bot, bot.block_manager)
            bot.tree.add_command(cog.block)
            bot.tree.add_command(cog.unblock)
            interaction = MagicMock(spec=discord.Interaction)
            interaction.client = bot
            interaction._state = bot._connection
            interaction.type = discord.InteractionType.application_command
            interaction.guild_id = 42
            interaction.guild = MagicMock(spec=discord.Guild, id=42, name="Guild")
            interaction.user = MagicMock(spec=discord.Member, id=99)
            interaction.permissions = discord.Permissions(administrator=True)
            interaction.namespace = discord.app_commands.Namespace(interaction, {}, [])

            async def defer(**_kwargs: object) -> None:
                interaction.response.is_done.return_value = True
                interaction.response.type = (
                    discord.InteractionResponseType.deferred_channel_message
                )

            for command, is_blocked, reason in (
                (cog.block, True, "Repeated spam"),
                (cog.unblock, False, "Appeal accepted"),
            ):
                with self.subTest(command=command.name):
                    interaction.command = command
                    interaction.command_failed = False
                    interaction.extras = {}
                    interaction.response = MagicMock(spec=discord.InteractionResponse)
                    interaction.response.is_done.return_value = False
                    interaction.response.defer = AsyncMock(side_effect=defer)
                    interaction.edit_original_response = AsyncMock()
                    interaction.data = {
                        "name": command.name,
                        "type": 1,
                        "options": [
                            {"name": "user", "type": 6, "value": "31"},
                            {"name": "reason", "type": 3, "value": reason},
                        ],
                        "resolved": {
                            "users": {
                                "31": {
                                    "id": "31",
                                    "username": "Target",
                                    "discriminator": "0",
                                    "avatar": None,
                                }
                            },
                            "members": {
                                "31": {"flags": 0, "roles": [], "joined_at": None}
                            },
                        },
                    }

                    await bot.tree._call(interaction)

                    self.assertFalse(interaction.command_failed)
                    interaction.response.defer.assert_awaited_once_with(
                        thinking=True, ephemeral=True
                    )
                    interaction.edit_original_response.assert_awaited_once()
                    reply = interaction.edit_original_response.await_args
                    if reply is None:
                        self.fail("Expected a private moderation reply")
                    embed = reply.kwargs["embed"]
                    if not isinstance(embed, discord.Embed):
                        self.fail("Expected an embed in the moderation reply")
                    self.assertIn("<@31>", embed.description or "")
                    self.assertEqual([field.value for field in embed.fields], [reason])
                    user = await bot.block_manager.get_user(42, 31)
                    if user is None:
                        self.fail("Expected a committed block history for the target")
                    self.assertEqual(user.is_blocked, is_blocked)
                    history = user.block_history if is_blocked else user.unblock_history
                    self.assertEqual(
                        [(entry.admin_id, entry.reason) for entry in history],
                        [(99, reason)],
                    )


class TestBlockedPages(unittest.TestCase):
    def test_english_labels_keep_names_and_audit_reasons_unchanged(self) -> None:
        user = BlockedUser(
            1,
            "Иван",
            None,
            block_history=[
                BlockHistoryEntry(
                    99, "Повторный спам", datetime(2026, 1, 1, tzinfo=UTC)
                )
            ],
            blocked=True,
        )
        entry = format_blocked_user(user, None, show_details=True, language=Language.EN)
        pages = BlockedListPages([entry], show_details=True, language=Language.EN)
        embed = pages.make_embed(0)
        self.assertEqual(embed.title, "Blocked users (1)")
        self.assertIn("Current name: Иван", embed.description or "")
        self.assertIn("Reason: Повторный спам", embed.description or "")

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
    async def test_missing_block_history_keeps_user_and_current_name(self) -> None:
        user = BlockedUser(1, "Imported name", None, blocked=True)
        manager = MagicMock(get_guild_users=AsyncMock(return_value=[user]))
        cog = AdminCog(MagicMock(), manager)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock(spec=discord.InteractionResponse)
        interaction.guild.get_member.return_value = None

        await cog.listblocked._do_call(
            interaction, {"show_details": True, "ephemeral": True}
        )

        interaction.response.send_message.assert_awaited_once()
        sent = interaction.response.send_message.await_args
        if sent is None:
            self.fail("Expected the blocked-user list")
        embed = sent.kwargs["embed"]
        self.assertEqual(embed.title, "Заблокированные пользователи (1)")
        self.assertIn("Пользователь покинул сервер `1`", embed.description)
        self.assertIn("• Текущее имя: Imported name", embed.description)
        self.assertIn("История блокировок отсутствует.", embed.description)
        self.assertNotIn("Последняя блокировка:", embed.description)
        self.assertNotIn("Администратор:", embed.description)
        self.assertTrue(sent.kwargs["ephemeral"])

    async def test_sends_blocked_members_and_departed_users_with_details(self) -> None:
        moment = datetime(2026, 1, 1, tzinfo=UTC)
        present = BlockedUser(
            1,
            "Stored name",
            None,
            block_history=[BlockHistoryEntry(99, "Present reason", moment)],
            blocked=True,
        )
        departed = BlockedUser(
            2,
            "Departed name",
            None,
            block_history=[BlockHistoryEntry(99, "Departed reason", moment)],
            blocked=True,
        )
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
