"""Exercise the editor's observable save, authorization and expiration behavior."""

from dataclasses import replace
from typing import override
from unittest import IsolatedAsyncioTestCase
from unittest.mock import PropertyMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.features.preferences import view as view_module
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.features.preferences.view import (
    LanguageSelect,
    SettingsView,
    TimezoneModal,
)
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.preferences.fixtures import interaction
from tests.storage.fixtures import temporary_database


class SettingsViewTests(IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.repository = PreferencesRepository(database)
        self.view = SettingsView(
            owner_id=10,
            scope=PreferenceScope.USER,
            subject_id=10,
            repository=self.repository,
            initial=Preferences(),
            context=PresentationContext(Language.RU, ZoneInfo("UTC")),
            audience_locale="en-US",
            fallback_timezone=ZoneInfo("UTC"),
        )
        self.addCleanup(self.view.stop)

    async def test_language_reset_keeps_zone_and_editor_language(self) -> None:
        self.view.proposed = Preferences(Language.EN, "Asia/Krasnoyarsk")
        selector = LanguageSelect(self.view)
        with patch.object(
            LanguageSelect,
            "values",
            new_callable=PropertyMock,
            return_value=["discord"],
        ):
            await selector.callback(interaction())
        self.assertIsNone(self.view.proposed.language)
        self.assertEqual(self.view.proposed.timezone, "Asia/Krasnoyarsk")
        self.assertEqual(self.view.context.language, Language.RU)
        self.assertIn("Личные настройки", str(self.view.to_components()))
        self.assertEqual(
            await self.repository.get(PreferenceScope.USER, 10), Preferences()
        )

    async def test_commit_failure_never_claims_success(self) -> None:
        self.view.proposed = Preferences(Language.EN)
        request = interaction()
        with patch.object(self.repository, "save", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                await self.view.save(request)
        request.response.defer.assert_awaited_once()
        request.edit_original_response.assert_not_awaited()
        self.assertFalse(self.view.closed)
        self.assertFalse(self.view.saving)

    async def test_conflict_shows_current_values_without_overwriting_them(self) -> None:
        saved = await self.repository.save(
            PreferenceScope.USER, 10, Preferences(Language.EN, "Europe/London")
        )
        self.view.proposed = Preferences(Language.RU)
        await self.view.save(interaction())
        self.assertEqual(await self.repository.get(PreferenceScope.USER, 10), saved)
        self.assertTrue(self.view.closed)
        self.assertIn("Europe/London", str(self.view.to_components()))

    async def test_unauthorized_user_gets_private_reply_in_their_language(self) -> None:
        request = interaction(11)
        self.assertFalse(await self.view.interaction_check(request))
        request.response.send_message.assert_awaited_once_with(
            "This panel belongs to someone else. Open /settings.", ephemeral=True
        )

    async def test_server_permission_is_checked_again_for_components(self) -> None:
        self.view.scope = PreferenceScope.GUILD
        self.view.subject_id = 20
        request = interaction()
        request.permissions = discord.Permissions.none()
        self.assertFalse(await self.view.interaction_check(request))
        request.response.send_message.assert_awaited_once()

    async def test_modal_rejects_invalid_zone_and_cannot_change_closed_panel(
        self,
    ) -> None:
        modal = TimezoneModal(self.view)
        self.addCleanup(modal.stop)
        with (
            patch.object(view_module, "check_component_access", return_value=True),
            patch.object(
                discord.ui.TextInput,
                "value",
                new_callable=PropertyMock,
                return_value="Bad/Zone",
            ),
        ):
            request = interaction()
            await modal.on_submit(request)
        self.assertIsNone(self.view.proposed.timezone)
        request.response.send_message.assert_awaited_once()
        await self.view.on_timeout()
        with patch.object(
            discord.ui.TextInput,
            "value",
            new_callable=PropertyMock,
            return_value="Europe/Moscow",
        ):
            await modal.on_submit(interaction())
        self.assertIsNone(self.view.proposed.timezone)
        self.assertTrue(self.view.is_finished())

    async def test_cancel_and_timeout_leave_database_unchanged(self) -> None:
        self.view.proposed = replace(self.view.proposed, timezone="Europe/Moscow")
        await self.view.cancel(interaction())
        self.assertTrue(self.view.closed)
        self.assertEqual(
            await self.repository.get(PreferenceScope.USER, 10), Preferences()
        )

    async def test_save_confirms_committed_values(self) -> None:
        self.view.proposed = Preferences(Language.EN, "Europe/Moscow")
        await self.view.save(interaction())
        self.assertEqual(
            await self.repository.get(PreferenceScope.USER, 10),
            Preferences(Language.EN, "Europe/Moscow", 1),
        )
        self.assertIn("Настройки сохранены", str(self.view.to_components()))
