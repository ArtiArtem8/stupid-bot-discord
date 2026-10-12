"""Current blocking state across commands and sensitive component entrypoints."""

import unittest
from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from stupidbot.discord_ui.access import check_command_access
from stupidbot.discord_ui.exceptions import BlockedUserError
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.access.access_cog import AdminCog
from stupidbot.features.access.operations import BlockManager
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.birthdays.views import ConfirmDeleteView
from stupidbot.features.music.discord.views.controller import TrackControllerView
from stupidbot.features.music.discord.views.queue import (
    QueuePaginationAdapter,
    QueuePaginator,
    QueueUndoView,
)
from stupidbot.features.music.discord.views.session import SessionSummaryView
from stupidbot.features.music.playback.models import PlaybackAttempt
from stupidbot.features.music.runtime.session import MusicSession
from stupidbot.features.voice.discord.profile_cog import VoiceProfileCog
from stupidbot.features.voice.discord.view import ProfileAction, VoiceProfileView
from stupidbot.features.wolfram.wolfram_cog import WolframCog
from tests.features.music.fixtures import make_entry


class TestAuthorization(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.item = MagicMock(spec=discord.Interaction)
        self.item.guild = MagicMock(spec=discord.Guild, id=42, name="guild")
        self.item.user = MagicMock(spec=discord.Member, id=10, name="user")
        self.item.created_at = datetime.now(UTC)
        self.item.response = MagicMock(spec=discord.InteractionResponse)
        self.item.delete_original_response = AsyncMock()
        self.blocked = AsyncMock(return_value=True)
        self.feedback = AsyncMock()
        self.manager = MagicMock(spec=BlockManager)
        self.item.client = MagicMock()
        self.item.client.block_manager = self.manager
        self.block_patch = patch.object(self.manager, "is_user_blocked", self.blocked)
        self.block_patch.start()
        self.addCleanup(self.block_patch.stop)
        feedback_patch = patch.object(FeedbackUI, "send", self.feedback)
        feedback_patch.start()
        self.addCleanup(feedback_patch.stop)

    async def test_predicate_uses_current_state_and_allows_direct_messages(
        self,
    ) -> None:
        with self.assertRaises(BlockedUserError):
            await check_command_access(self.item)
        self.blocked.assert_awaited_once_with(42, 10)
        self.blocked.return_value = False
        self.assertTrue(await check_command_access(self.item))
        self.item.guild = None
        self.blocked.reset_mock()
        self.assertTrue(await check_command_access(self.item))
        self.blocked.assert_not_awaited()

    async def test_voice_slash_and_context_menu_reject_before_dispatch(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        voice = VoiceProfileCog(bot)
        wolfram = WolframCog(bot)
        for command in (voice.voice_profile, wolfram.ctx_menu):
            with self.subTest(command=command.name):
                with self.assertRaises(BlockedUserError):
                    await command._check_can_run(self.item)
        self.item.response.defer.assert_not_awaited()

    async def test_admin_bypass_does_not_consult_blocking(self) -> None:
        self.assertTrue(
            await AdminCog(MagicMock(), self.manager).interaction_check(self.item)
        )
        self.blocked.assert_not_awaited()

    async def test_refresh_checks_new_block_after_card_creation_but_delete_is_allowed(
        self,
    ) -> None:
        refresh = AsyncMock()
        view = VoiceProfileView(10, refresh, reveal=AsyncMock(), private=False)
        self.assertTrue(await view.interaction_check(self.item))
        await view.buttons[ProfileAction.REFRESH].callback(self.item)
        refresh.assert_not_awaited()
        self.item.response.defer.assert_not_awaited()
        self.feedback.assert_awaited_once()
        self.assertFalse(view.is_finished())
        await view.buttons[ProfileAction.DELETE].callback(self.item)
        self.item.delete_original_response.assert_awaited_once()

    async def test_controller_denial_keeps_view_and_player_intact(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt)
        player.seek_attempt = AsyncMock()
        stopped = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=42,
            attempt=attempt,
            on_stop_callback=stopped,
            on_player_failure=AsyncMock(),
        )
        button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.custom_id == "btn_restart"
        )
        await button.callback(self.item)
        player.seek_attempt.assert_not_awaited()
        stopped.assert_not_awaited()
        self.assertFalse(view.is_finished())
        self.feedback.assert_awaited_once()

    async def test_queue_undo_and_refresh_do_not_run_callbacks_when_blocked(
        self,
    ) -> None:
        remove = AsyncMock()
        refresh = AsyncMock()
        undo = QueueUndoView(
            guild_id=42,
            expected_entries=(),
            requester_id=10,
            remove_callback=remove,
            timeout=60,
        )
        paginator = QueuePaginator(MagicMock(spec=QueuePaginationAdapter), refresh, 10)
        await undo.remove(self.item)
        await paginator.refresh(self.item)
        remove.assert_not_awaited()
        refresh.assert_not_awaited()
        self.assertFalse(undo.is_finished())
        self.assertFalse(paginator.is_finished())

    async def test_history_denial_does_not_open_paginator(self) -> None:
        view = SessionSummaryView(session=MagicMock(spec=MusicSession))
        await view.view_full_button.callback(self.item)
        self.feedback.assert_awaited_once()
        self.item.response.send_message.assert_not_awaited()

    async def test_birthday_confirm_does_not_modify_registration_when_blocked(
        self,
    ) -> None:
        view = ConfirmDeleteView(10, 42, MagicMock(spec=BirthdayRepository), 1)
        with patch.object(
            view.manager, "clear_user_birthday", new=AsyncMock()
        ) as clear:
            await view.confirm.callback(self.item)
        clear.assert_not_awaited()
        self.feedback.assert_awaited_once()
