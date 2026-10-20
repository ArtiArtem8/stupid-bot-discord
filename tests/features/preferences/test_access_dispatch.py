"""Preference controls keep their initial response budget and response audience."""

import asyncio
from datetime import UTC, datetime
from typing import override
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
from discord.webhook.async_ import async_context

from stupidbot.discord_ui.pagination import CallbackButton
from stupidbot.features.preferences import reminders
from stupidbot.features.preferences import view as view_module
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.reminders import ReminderView
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.features.preferences.view import (
    SettingsView,
    TimezoneModal,
    TimezoneSelect,
)
from stupidbot.i18n import discord as discord_i18n
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.preferences.fixtures import interaction
from tests.storage.fixtures import temporary_database


class PreferenceAccessDispatchTests(IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.repository = PreferencesRepository(database)
        self.context = PresentationContext(Language.EN, ZoneInfo("UTC"))
        self.settings = SettingsView(
            owner_id=10,
            scope=PreferenceScope.USER,
            subject_id=10,
            repository=self.repository,
            initial=Preferences(Language.EN, "Asia/Seoul"),
            context=self.context,
            audience_locale="en-US",
            fallback_timezone=ZoneInfo("UTC"),
        )
        self.addCleanup(self.settings.stop)
        self.save = next(
            item
            for item in self.settings.walk_children()
            if isinstance(item, CallbackButton) and item.label == "Save"
        )
        self.timezone = next(
            item
            for item in self.settings.walk_children()
            if isinstance(item, TimezoneSelect)
        )
        self.reminder = await self._reminder(10)

    async def _reminder(self, user_id: int) -> ReminderView:
        now = datetime.now(UTC)
        await self.repository.reserve_prompt_if_due(user_id, now)
        reservation = await self.repository.reserve_prompt_if_due(user_id, now)
        if reservation is None:
            self.fail("Expected a committed reminder reservation")
        view = ReminderView(
            self.repository, reservation, self.context, command="/customize-me"
        )
        self.addCleanup(view.stop)
        return view

    def _request(self, user_id: int = 10) -> MagicMock:
        request = interaction(user_id)
        request.id = 123
        request.type = discord.InteractionType.component
        request.guild.id = 20
        request.client.preferences_repository = self.repository
        request.response = discord.InteractionResponse(request)
        request.data = {"values": ["other"]}
        return request

    async def _dispatch[ViewT: discord.ui.View | discord.ui.LayoutView](
        self,
        view: ViewT,
        item: discord.ui.Item[ViewT],
        request: MagicMock,
    ) -> MagicMock:
        adapter = MagicMock()
        adapter.create_interaction_response = AsyncMock(
            return_value={"interaction": {"id": str(request.id)}}
        )
        token = async_context.set(adapter)
        try:
            await asyncio.wait_for(view._scheduled_task(item, request), timeout=2)
        finally:
            async_context.reset(token)
        return adapter

    async def test_settings_access_timeout_preserves_pending_values_and_modal_slot(
        self,
    ) -> None:
        for item in (self.save, self.timezone):
            with self.subTest(action=type(item).__name__):
                request = self._request()
                cancelled = asyncio.Event()

                async def access(
                    _guild: int,
                    _user: int,
                    *,
                    cancellation: asyncio.Event = cancelled,
                ) -> bool:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancellation.set()
                    return False

                request.client.block_manager.is_user_blocked.side_effect = access
                with patch.object(view_module, "MODAL_ACCESS_TIMEOUT", 0):
                    adapter = await self._dispatch(self.settings, item, request)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(
                    adapter.create_interaction_response.call_args.kwargs[
                        "params"
                    ].payload,
                    {"type": 5, "data": {"flags": 64}},
                )
                request.edit_original_response.assert_awaited_once()
                self.assertIn(
                    "Could not check access",
                    request.edit_original_response.call_args.kwargs[
                        "embed"
                    ].description,
                )
                self.assertEqual(
                    self.settings.proposed, Preferences(Language.EN, "Asia/Seoul")
                )
                self.assertFalse(self.settings.is_finished())
                self.assertEqual(
                    await self.repository.get(PreferenceScope.USER, 10), Preferences()
                )

    async def test_reminder_access_timeout_does_not_consume_the_reservation(
        self,
    ) -> None:
        for index, item in enumerate(self.reminder.children):
            with self.subTest(action=index):
                request = self._request()
                cancelled = asyncio.Event()

                async def access(
                    _guild: int,
                    _user: int,
                    *,
                    cancellation: asyncio.Event = cancelled,
                ) -> bool:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancellation.set()
                    return False

                request.client.block_manager.is_user_blocked.side_effect = access
                with patch.object(reminders, "MODAL_ACCESS_TIMEOUT", 0):
                    adapter = await self._dispatch(self.reminder, item, request)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(
                    adapter.create_interaction_response.call_args.kwargs[
                        "params"
                    ].payload,
                    {"type": 5, "data": {"flags": 64}},
                )
                request.edit_original_response.assert_awaited_once()
                self.assertFalse(self.reminder.is_finished())
        self.assertTrue(
            await self.repository.answer_prompt(self.reminder.reservation, never=False)
        )

    async def test_wrong_owner_feedback_bounds_its_preferences_lookup(self) -> None:
        for view, item in (
            (self.settings, self.save),
            (self.reminder, self.reminder.children[0]),
        ):
            with self.subTest(view=type(view).__name__):
                request = self._request(11)
                cancelled = asyncio.Event()

                async def preferences(
                    _scope: PreferenceScope,
                    _subject_id: int,
                    *,
                    cancellation: asyncio.Event = cancelled,
                ) -> Preferences:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancellation.set()
                    return Preferences()

                with (
                    patch.object(
                        self.repository, "get", side_effect=preferences
                    ) as read,
                    patch.object(discord_i18n, "ERROR_PREFERENCES_TIMEOUT", 0),
                    self.assertLogs("stupidbot.i18n.discord", level="WARNING"),
                ):
                    adapter = await self._dispatch(view, item, request)
                self.assertTrue(cancelled.is_set())
                read.assert_awaited_once_with(PreferenceScope.USER, 11)
                request.client.block_manager.is_user_blocked.assert_not_awaited()
                payload = adapter.create_interaction_response.call_args.kwargs[
                    "params"
                ].payload
                self.assertEqual(payload["type"], 4)
                self.assertEqual(payload["data"]["flags"], 64)
                self.assertIn("belongs to someone else", payload["data"]["content"])
                self.assertFalse(view.is_finished())

    async def test_allowed_timezone_dispatch_opens_modal_without_defer(self) -> None:
        request = self._request()
        adapter = await self._dispatch(self.settings, self.timezone, request)
        adapter.create_interaction_response.assert_awaited_once()
        payload = adapter.create_interaction_response.call_args.kwargs["params"].payload
        self.assertEqual(payload["type"], 9)
        modal = request._state.store_view.call_args.args[0]
        if not isinstance(modal, TimezoneModal):
            self.fail("Expected the timezone modal")
        self.addCleanup(modal.stop)
        request.edit_original_response.assert_not_awaited()

    async def test_reminder_open_keeps_a_separate_private_editor(self) -> None:
        request = self._request()
        adapter = await self._dispatch(
            self.reminder, self.reminder.children[0], request
        )
        self.assertEqual(
            adapter.create_interaction_response.call_args.kwargs["params"].payload,
            {"type": 5, "data": {"flags": 64}},
        )
        panel = request.edit_original_response.call_args.kwargs["view"]
        if not isinstance(panel, SettingsView):
            self.fail("Expected a separate settings editor")
        self.addCleanup(panel.stop)
        self.assertFalse(self.reminder.is_finished())

    async def test_reminder_answers_still_update_the_source_message(self) -> None:
        for button_index in (1, 2):
            with self.subTest(button_index=button_index):
                user_id = 10 + button_index
                reminder = await self._reminder(user_id)
                request = self._request(user_id)
                adapter = await self._dispatch(
                    reminder, reminder.children[button_index], request
                )
                self.assertEqual(
                    adapter.create_interaction_response.call_args.kwargs[
                        "params"
                    ].payload,
                    {"type": 6},
                )
                self.assertTrue(reminder.is_finished())
                self.assertIsNone(
                    request.edit_original_response.call_args.kwargs["view"]
                )
                self.assertFalse(
                    await self.repository.answer_prompt(
                        reminder.reservation, never=False
                    )
                )
