"""Optional reminders never turn a completed profile into a failed command."""

from datetime import UTC, datetime, timedelta
from typing import override
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.reminders import ReminderView, maybe_remind
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.preferences.fixtures import interaction
from tests.storage.fixtures import temporary_database


class ReminderTests(IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.repository = PreferencesRepository(database)
        self.request = interaction()
        self.request.client = MagicMock(spec=commands.Bot)
        self.request.client.tree.fetch_commands = AsyncMock(return_value=[])
        self.request.client.preferences_repository = self.repository
        self.request.followup.send = AsyncMock()
        self.context = PresentationContext(Language.EN, ZoneInfo("UTC"))

    async def test_second_successful_use_sends_only_a_private_followup(self) -> None:
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_not_awaited()
        self.request.client.tree.fetch_commands.assert_not_awaited()
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_awaited_once()
        call = self.request.followup.send.await_args
        if call is None:
            self.fail("Expected a reminder")
        self.assertTrue(call.kwargs["ephemeral"])
        self.assertIn("/customize-me", call.args[0])
        self.request.client.tree.fetch_commands.assert_awaited_once_with()
        view = call.kwargs["view"]
        self.assertIsInstance(view, ReminderView)
        if isinstance(view, ReminderView):
            view.stop()
        self.assertEqual(
            await self.repository.get(PreferenceScope.USER, 10), Preferences()
        )

    async def test_reminder_and_dismissal_keep_command_mention_in_every_language(
        self,
    ) -> None:
        command = MagicMock(spec=discord.app_commands.AppCommand)
        command.name = "customize-me"
        command.type = discord.AppCommandType.chat_input
        command.mention = "</customize-me:123456789012345678>"
        self.request.client.tree.fetch_commands.return_value = [command]
        for language in Language:
            for never in (False, True):
                with self.subTest(language=language, never=never):
                    self.request.user.id += 1
                    context = PresentationContext(language, ZoneInfo("UTC"))
                    await maybe_remind(self.request, context)
                    await maybe_remind(self.request, context)
                    call = self.request.followup.send.await_args
                    if call is None:
                        self.fail("Expected a reminder")
                    self.assertIn(command.mention, call.args[0])
                    view = call.kwargs["view"]
                    if not isinstance(view, ReminderView):
                        self.fail("Expected reminder controls")
                    if never:
                        await view.never(self.request)
                    else:
                        await view.later(self.request)
                    reply = self.request.edit_original_response.await_args
                    if reply is None:
                        self.fail("Expected dismissal feedback")
                    self.assertIn(command.mention, reply.kwargs["content"])
                    self.assertIsNone(reply.kwargs["view"])
                    self.assertTrue(view.is_finished())

    async def test_command_lookup_failure_preserves_the_reminder(self) -> None:
        self.request.client.tree.fetch_commands.side_effect = discord.HTTPException(
            MagicMock(status=503, reason="Unavailable"), "unavailable"
        )
        await maybe_remind(self.request, self.context)
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_awaited_once()
        call = self.request.followup.send.await_args
        if call is None:
            self.fail("Expected a reminder")
        self.assertIn("/customize-me", call.args[0])
        view = call.kwargs["view"]
        if not isinstance(view, ReminderView):
            self.fail("Expected reminder controls")
        await view.later(self.request)
        reply = self.request.edit_original_response.await_args
        if reply is None:
            self.fail("Expected dismissal feedback")
        self.assertIn("/customize-me", reply.kwargs["content"])

    async def test_uncertain_send_is_not_retried_and_storage_failure_is_isolated(
        self,
    ) -> None:
        await maybe_remind(self.request, self.context)
        self.request.followup.send.side_effect = OSError("transport failed")
        await maybe_remind(self.request, self.context)
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_awaited_once()
        with patch.object(
            self.repository,
            "reserve_prompt_if_due",
            side_effect=OSError("storage failed"),
        ):
            await maybe_remind(self.request, self.context)

    async def test_explicit_default_save_stops_onboarding_without_revision_change(
        self,
    ) -> None:
        saved = await self.repository.save(PreferenceScope.USER, 10, Preferences())
        self.assertEqual(saved.revision, 0)
        for day in (0, 1, 15, 90):
            self.assertIsNone(
                await self.repository.reserve_prompt_if_due(
                    10, datetime(2026, 10, 12, tzinfo=UTC) + timedelta(days=day)
                )
            )
