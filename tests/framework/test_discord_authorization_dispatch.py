"""Authorization through discord.py's registered command and component dispatch."""

import unittest
from datetime import UTC, datetime, timedelta
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.blocking import block_manager
from cogs.guild_monitor_cog import ServerMonitorCog
from cogs.voice.profile.view import ProfileAction, VoiceProfileView
from cogs.voice.profile_cog import VoiceProfileCog
from cogs.wolfram_cog import WolframCog
from framework.bot import StupidBot


class TestDiscordAuthorizationDispatch(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.bot = StupidBot()
        self.addAsyncCleanup(self.bot.close)
        self.item = MagicMock(spec=discord.Interaction)
        self.item._state = self.bot._connection
        self.item.client = self.bot
        self.item.guild = MagicMock(spec=discord.Guild, id=42, name="Guild")
        self.item.guild_id = 42
        self.item.user = MagicMock(spec=discord.Member, id=9001, name="User")
        self.item.created_at = datetime.now(UTC)
        self.item.type = discord.InteractionType.application_command
        self.item.command_failed = False
        self.item.response = MagicMock(spec=discord.InteractionResponse)
        self.item.response.is_done.return_value = False
        channel = discord.PartialMessageable(
            state=self.bot._connection, id=44, guild_id=42
        )
        self.item.guild.get_channel_or_thread.return_value = channel
        self.blocked = AsyncMock(return_value=True)
        blocked_patch = patch.object(block_manager, "is_user_blocked", self.blocked)
        blocked_patch.start()
        self.addCleanup(blocked_patch.stop)

    def _context_payload(self) -> None:
        self.item.data = {
            "name": "Solve with Wolfram",
            "type": 3,
            "target_id": "55",
            "resolved": {
                "messages": {
                    "55": {
                        "id": "55",
                        "channel_id": "44",
                        "content": "sin(x)",
                        "author": {
                            "id": "9001",
                            "username": "User",
                            "discriminator": "0",
                            "avatar": None,
                        },
                        "timestamp": "2026-10-01T00:00:00+00:00",
                        "edited_timestamp": None,
                        "tts": False,
                        "mention_everyone": False,
                        "mentions": [],
                        "mention_roles": [],
                        "attachments": [],
                        "embeds": [],
                        "pinned": False,
                        "type": 0,
                        "flags": 0,
                    }
                }
            },
        }

    def _assert_denied_once(self) -> None:
        self.item.response.defer.assert_not_awaited()
        self.item.response.send_message.assert_awaited_once()
        kwargs = self.item.response.send_message.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["embed"].description, "⛔ Доступ к командам запрещён.")

    async def test_registered_context_menu_routes_denial_without_running_callback(
        self,
    ) -> None:
        cog = WolframCog(self.bot)
        self.item.command = cog.ctx_menu
        self._context_payload()
        with patch.object(cog, "_handle_query", new=AsyncMock()) as query:
            await self.bot.tree._call(self.item)
        self.blocked.assert_awaited_once_with(42, 9001)
        query.assert_not_awaited()
        self._assert_denied_once()

        # The menu's existing cooldown runs before the access check.
        self.item.created_at += timedelta(seconds=6)
        self.blocked.return_value = False
        with (
            patch.object(cog, "_handle_query", new=AsyncMock()) as query,
            patch.object(self.bot, "dispatch") as dispatch,
        ):
            await self.bot.tree._call(self.item)
        query.assert_awaited_once_with(self.item, "sin(x)", mode="solve")
        self.item.response.defer.assert_awaited_once_with(ephemeral=True)
        dispatch.assert_called_once()

    async def test_registered_voice_slash_routes_denial_before_render(self) -> None:
        cog = VoiceProfileCog(self.bot)
        self.bot.tree.add_command(cog.voice_profile)
        self.item.command = cog.voice_profile
        self.item.data = {"name": "voice-profile", "type": 1, "options": []}
        with patch.object(cog, "_deliver", new=AsyncMock()) as deliver:
            await self.bot.tree._call(self.item)
        deliver.assert_not_awaited()
        self._assert_denied_once()

    async def test_registered_monitor_group_uses_cog_check(self) -> None:
        cog = ServerMonitorCog(self.bot)
        self.bot.tree.add_command(cog.monitor)
        self.item.command = cog.monitor_status
        self.item.data = {
            "name": "monitor",
            "type": 1,
            "options": [{"name": "status", "type": 1, "options": []}],
        }
        await self.bot.tree._call(self.item)
        self.blocked.assert_awaited_once_with(42, 9001)
        self._assert_denied_once()

    async def test_view_dispatch_enforces_owner_and_current_block(self) -> None:
        refresh = AsyncMock()
        view = VoiceProfileView(9001, refresh, reveal=AsyncMock())
        self.item.data = {
            "custom_id": view.buttons[ProfileAction.REFRESH].custom_id,
            "component_type": 2,
        }
        await view._scheduled_task(view.buttons[ProfileAction.REFRESH], self.item)
        refresh.assert_not_awaited()
        self._assert_denied_once()
        self.assertFalse(view.is_finished())
        self.item.user.id = 9002
        self.blocked.reset_mock()
        self.item.response.send_message.reset_mock()
        await view._scheduled_task(view.buttons[ProfileAction.REFRESH], self.item)
        self.blocked.assert_not_awaited()
        self.item.response.send_message.assert_awaited_once_with(
            "Это не ваша карточка.", ephemeral=True
        )
