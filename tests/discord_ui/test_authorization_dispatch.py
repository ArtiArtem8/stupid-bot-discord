"""Authorization through discord.py's registered command and component dispatch."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui import base_cog
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.birthdays.birthday_cog import BirthdayCog
from stupidbot.features.member_roles.member_roles_cog import ServerMonitorCog
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.preferences_cog import PreferencesCog
from stupidbot.features.reports.report_cog import ReportCog
from stupidbot.features.voice.discord.profile_cog import VoiceProfileCog
from stupidbot.features.voice.discord.view import ProfileAction, VoiceProfileView
from stupidbot.features.wolfram.wolfram_cog import WolframCog
from stupidbot.i18n.policy import Language, PresentationContext


class TestDiscordAuthorizationDispatch(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.bot = StupidBot()
        self.addAsyncCleanup(self.bot.close)
        self.enterContext(
            patch.object(FeedbackUI, "_default_report_callback", AsyncMock())
        )
        self.item = MagicMock(spec=discord.Interaction)
        self.item._state = self.bot._connection
        self.item.client = self.bot
        self.item.guild = MagicMock(spec=discord.Guild, id=42, name="Guild")
        self.item.guild_id = 42
        self.item.user = MagicMock(spec=discord.Member, id=9001, name="User")
        self.item.created_at = datetime.now(UTC)
        self.item.type = discord.InteractionType.application_command
        self.item.command_failed = False
        self.item.extras = {}
        self._reset_response()
        channel = discord.PartialMessageable(
            state=self.bot._connection, id=44, guild_id=42
        )
        self.item.guild.get_channel_or_thread.return_value = channel
        self.blocked = AsyncMock(return_value=True)
        blocked_patch = patch.object(
            self.bot.block_manager, "is_user_blocked", self.blocked
        )
        blocked_patch.start()
        self.addCleanup(blocked_patch.stop)

    def _reset_response(self) -> None:
        self.item.response = MagicMock(spec=discord.InteractionResponse)
        self.item.response.is_done.return_value = False
        self.item.response.type = None

        async def defer(**_kwargs: object) -> None:
            self.item.response.is_done.return_value = True
            self.item.response.type = (
                discord.InteractionResponseType.deferred_channel_message
            )

        self.item.response.defer.side_effect = defer
        self.item.edit_original_response = AsyncMock(
            return_value=MagicMock(delete=AsyncMock())
        )
        self.item.followup.send = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
        self.item.delete_original_response = AsyncMock()

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
        self.item.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        self.item.response.send_message.assert_not_awaited()
        self.item.edit_original_response.assert_awaited_once()
        kwargs = self.item.edit_original_response.call_args.kwargs
        self.assertEqual(kwargs["embed"].description, "Доступ к командам запрещён.")

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
        self._reset_response()
        self.blocked.return_value = False
        with (
            patch.object(cog, "_handle_query", new=AsyncMock()) as query,
            patch.object(self.bot, "dispatch") as dispatch,
        ):
            await self.bot.tree._call(self.item)
        query.assert_awaited_once_with(self.item, "sin(x)", mode="solve")
        self.item.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
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
        cog = ServerMonitorCog(self.bot, self.bot.monitor_manager)
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
        view = VoiceProfileView(
            9001,
            refresh,
            reveal=AsyncMock(),
            context=PresentationContext(Language.RU, ZoneInfo("UTC")),
        )
        self.item.data = {
            "custom_id": view.buttons[ProfileAction.REFRESH].custom_id,
            "component_type": 2,
        }
        await view._scheduled_task(view.buttons[ProfileAction.REFRESH], self.item)
        refresh.assert_not_awaited()
        self._assert_denied_once()
        self.assertFalse(view.is_finished())
        self.item.user.id = 9002
        self._reset_response()
        self.blocked.reset_mock()
        self.item.response.send_message.reset_mock()
        await view._scheduled_task(view.buttons[ProfileAction.REFRESH], self.item)
        self.blocked.assert_not_awaited()
        self.item.response.send_message.assert_awaited_once_with(
            "Это не ваша карточка.", ephemeral=True
        )

    async def test_registered_command_acknowledges_before_waiting_for_access(
        self,
    ) -> None:
        cog = PreferencesCog(self.bot, self.bot.preferences_repository)
        self.bot.tree.add_command(cog.customize_me)
        self.item.command = cog.customize_me
        self.item.data = {"name": "customize-me", "type": 1, "options": []}
        checking = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_guild: int, _user: int) -> bool:
            self.item.response.defer.assert_awaited_once_with(
                thinking=True, ephemeral=True
            )
            checking.set()
            await release.wait()
            return True

        self.blocked.side_effect = blocked
        task = asyncio.create_task(self.bot.tree._call(self.item))
        try:
            await asyncio.wait_for(checking.wait(), 2)
            self.assertFalse(task.done())
            self.item.edit_original_response.assert_not_awaited()
        finally:
            release.set()
            await task
        self._assert_denied_once()

    async def test_rejected_ack_does_not_check_access_or_mutate_birthday(self) -> None:
        cog = BirthdayCog(self.bot, self.bot.birthday_repository)
        self.bot.tree.add_command(cog.set_birthday)
        self.item.command = cog.set_birthday
        self.item.data = {
            "name": "set-birthday",
            "type": 1,
            "options": [{"name": "date_input", "type": 3, "value": "2000-01-01"}],
        }
        self.item.response.defer.side_effect = discord.HTTPException(
            MagicMock(status=503, reason="Unavailable"), "test failure"
        )
        with (
            patch.object(cog.manager, "set_user_birthday", new=AsyncMock()) as save,
            self.assertLogs("stupidbot.discord_ui.errors", level="WARNING"),
        ):
            await self.bot.tree._call(self.item)
        self.blocked.assert_not_awaited()
        save.assert_not_awaited()
        self.item.response.defer.assert_awaited_once()
        self.item.edit_original_response.assert_not_awaited()

    async def test_registered_report_opens_modal_without_defer(self) -> None:
        cog = ReportCog(self.bot, self.bot.report_repository)
        self.bot.tree.add_command(cog.report)
        self.item.command = cog.report
        self.item.data = {"name": "report", "type": 1, "options": []}
        self.blocked.return_value = False
        with patch.object(
            self.bot.preferences_repository, "get", side_effect=OSError("unavailable")
        ):
            with self.assertLogs("stupidbot.i18n.discord", level="WARNING"):
                await self.bot.tree._call(self.item)
        self.item.response.defer.assert_not_awaited()
        self.item.response.send_modal.assert_awaited_once()
        self.item.edit_original_response.assert_not_awaited()

    async def test_report_access_timeout_does_not_open_modal(self) -> None:
        cog = ReportCog(self.bot, self.bot.report_repository)
        self.bot.tree.add_command(cog.report)
        self.item.command = cog.report
        self.item.data = {"name": "report", "type": 1, "options": []}
        cancelled = asyncio.Event()

        async def blocked(_guild: int, _user: int) -> bool:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return False

        self.blocked.side_effect = blocked
        with patch.object(base_cog, "MODAL_ACCESS_TIMEOUT", 0):
            await self.bot.tree._call(self.item)
        self.assertTrue(cancelled.is_set())
        self.item.response.send_modal.assert_not_awaited()
        self.item.edit_original_response.assert_awaited_once()

    async def test_access_storage_failure_reaches_global_error_handler(self) -> None:
        cog = PreferencesCog(self.bot, self.bot.preferences_repository)
        self.bot.tree.add_command(cog.customize_me)
        self.item.command = cog.customize_me
        self.item.data = {"name": "customize-me", "type": 1, "options": []}
        self.blocked.side_effect = OSError("private storage failure")
        with (
            patch.object(
                self.bot.preferences_repository, "get", return_value=Preferences()
            ),
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR"),
        ):
            await self.bot.tree._call(self.item)
        self.item.edit_original_response.assert_awaited_once()
        self.assertNotIn(
            "private storage failure",
            str(self.item.edit_original_response.call_args.kwargs),
        )

    async def test_preferences_wait_after_ack_and_error_resolves_same_response(
        self,
    ) -> None:
        cog = PreferencesCog(self.bot, self.bot.preferences_repository)
        self.bot.tree.add_command(cog.customize_me)
        self.item.command = cog.customize_me
        self.item.data = {"name": "customize-me", "type": 1, "options": []}
        self.blocked.return_value = False
        reading = asyncio.Event()
        release = asyncio.Event()

        async def preferences(_scope: PreferenceScope, _subject: int) -> Preferences:
            self.item.response.defer.assert_awaited_once()
            reading.set()
            await release.wait()
            raise OSError("private storage failure")

        with (
            patch.object(
                self.bot.preferences_repository, "get", side_effect=preferences
            ) as read,
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR"),
        ):
            task = asyncio.create_task(self.bot.tree._call(self.item))
            try:
                await asyncio.wait_for(reading.wait(), 2)
                self.assertFalse(task.done())
            finally:
                release.set()
                await task
        read.assert_awaited_once()
        self.item.response.send_message.assert_not_awaited()
        self.item.edit_original_response.assert_awaited_once()
