"""Birthday confirmation closes once, including concurrent clicks and failures."""

import asyncio
import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.discord_ui.exceptions import InteractionAcknowledgementError
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.birthdays.views import ConfirmDeleteView
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.preferences.fixtures import interaction


class TestBirthdayConfirmation(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.repository = MagicMock(spec=BirthdayRepository)
        self.repository.clear_user_birthday.return_value = (True, True)
        self.view = ConfirmDeleteView(
            10,
            20,
            self.repository,
            7,
            context=PresentationContext(Language.EN, ZoneInfo("UTC")),
        )
        self.addCleanup(self.view.stop)
        self.request = interaction()
        self.request.response.is_done.return_value = False
        self.message = MagicMock(spec=discord.InteractionMessage)
        self.view.message = self.message

    def _assert_closed_response(self) -> discord.Embed:
        self.assertTrue(self.view.is_finished())
        self.assertEqual(self.view.children, [])
        call = self.request.edit_original_response.await_args
        self.assertIsNotNone(call)
        if call is None:
            self.fail("Expected a final confirmation response")
        self.assertIsNone(call.kwargs["view"])
        embed: object = call.kwargs["embed"]
        if not isinstance(embed, discord.Embed):
            self.fail("Expected a result embed")
        return embed

    async def test_confirmation_acknowledges_before_access_and_versioned_deletion(
        self,
    ) -> None:
        async def clear(
            guild_id: int, user_id: int, *, expected_version: int
        ) -> tuple[bool, bool]:
            self.request.response.defer.assert_awaited_once_with(
                thinking=False, ephemeral=False
            )
            self.assertTrue(self.view.is_finished())
            self.assertEqual((guild_id, user_id, expected_version), (20, 10, 7))
            return True, True

        self.repository.clear_user_birthday.side_effect = clear
        await self.view.confirm.callback(self.request)
        embed = self._assert_closed_response()
        self.assertEqual(embed.description, "Your birthday has been removed")

    async def test_cancel_and_timeout_never_delete_registration(self) -> None:
        await self.view.cancel.callback(self.request)
        self.assertEqual(self._assert_closed_response().description, "Cancelled")
        await self.view.on_timeout()
        self.repository.clear_user_birthday.assert_not_awaited()
        self.message.edit.assert_not_awaited()

    async def test_timeout_removes_controls_and_ignores_queued_confirmation(
        self,
    ) -> None:
        await self.view.on_timeout()
        self.assertTrue(self.view.is_finished())
        self.message.edit.assert_awaited_once()
        call = self.message.edit.call_args
        self.assertIsNone(call.kwargs["view"])
        self.assertIn("expired", call.kwargs["embed"].description)
        await self.view.confirm.callback(self.request)
        self.repository.clear_user_birthday.assert_not_awaited()

    async def test_concurrent_confirmation_is_admitted_only_once(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def clear(
            _guild_id: int, _user_id: int, *, expected_version: int
        ) -> tuple[bool, bool]:
            self.assertEqual(expected_version, 7)
            started.set()
            await release.wait()
            return True, True

        self.repository.clear_user_birthday.side_effect = clear
        first = asyncio.create_task(self.view.confirm.callback(self.request))
        try:
            await asyncio.wait_for(started.wait(), 2)
            second = interaction()
            second.response.is_done.return_value = False
            await self.view.confirm.callback(second)
            await self.view.cancel.callback(second)
            second.edit_original_response.assert_not_awaited()
        finally:
            release.set()
            await first
        self.repository.clear_user_birthday.assert_awaited_once()
        self._assert_closed_response()

    async def test_cancellation_while_access_waits_prevents_pending_deletion(
        self,
    ) -> None:
        checking = asyncio.Event()
        release = asyncio.Event()

        async def access(_guild: int, _user: int) -> bool:
            checking.set()
            await release.wait()
            return False

        self.request.client.block_manager.is_user_blocked.side_effect = access
        first = asyncio.create_task(self.view.confirm.callback(self.request))
        try:
            await asyncio.wait_for(checking.wait(), 2)
            cancelled = interaction()
            cancelled.response.is_done.return_value = False
            await self.view.cancel.callback(cancelled)
        finally:
            release.set()
            await first
        self.assertTrue(self.view.is_finished())
        self.repository.clear_user_birthday.assert_not_awaited()

    async def test_stale_and_missing_configuration_are_terminal(self) -> None:
        for result, expected in (
            ((True, False), "already changed"),
            ((False, False), "configuration not found"),
        ):
            with self.subTest(result=result):
                await self.asyncSetUp()
                self.repository.clear_user_birthday.return_value = result
                await self.view.confirm.callback(self.request)
                self.assertIn(
                    expected, self._assert_closed_response().description or ""
                )

    async def test_storage_failure_closes_with_reopen_instruction(self) -> None:
        self.repository.clear_user_birthday.side_effect = OSError("private detail")
        with self.assertLogs("stupidbot.features.birthdays.views", level="ERROR"):
            await self.view.confirm.callback(self.request)
        embed = self._assert_closed_response()
        self.assertIn("/remove-birthday", embed.description or "")
        self.assertNotIn("private detail", embed.description or "")

    async def test_failed_final_edit_does_not_reopen_or_repeat_deletion(self) -> None:
        error = discord.HTTPException(
            MagicMock(status=503, reason="Unavailable"), "test failure"
        )
        self.request.edit_original_response.side_effect = error
        with self.assertRaises(discord.HTTPException):
            await self.view.confirm.callback(self.request)
        await self.view.confirm.callback(self.request)
        self.assertTrue(self.view.is_finished())
        self.repository.clear_user_birthday.assert_awaited_once()

    async def test_failed_ack_never_closes_or_deletes(self) -> None:
        self.request.response.defer.side_effect = discord.HTTPException(
            MagicMock(status=503, reason="Unavailable"), "test failure"
        )
        with self.assertRaises(InteractionAcknowledgementError):
            await self.view.confirm.callback(self.request)
        self.assertFalse(self.view.is_finished())
        self.repository.clear_user_birthday.assert_not_awaited()

    async def test_other_user_and_blocked_owner_leave_confirmation_open(self) -> None:
        other = interaction(11)
        other.response.is_done.return_value = False
        with patch(
            "stupidbot.features.birthdays.views.FeedbackUI.send", new=AsyncMock()
        ):
            await self.view.confirm.callback(other)
            self.request.client.block_manager.is_user_blocked.return_value = True
            await self.view.confirm.callback(self.request)
        self.assertFalse(self.view.is_finished())
        self.repository.clear_user_birthday.assert_not_awaited()
