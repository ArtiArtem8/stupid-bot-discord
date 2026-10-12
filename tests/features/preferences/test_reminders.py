"""Optional reminders never turn a completed profile into a failed command."""

from datetime import UTC, datetime, timedelta
from typing import override
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

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
        self.request.client.preferences_repository = self.repository
        self.request.followup.send = AsyncMock()
        self.context = PresentationContext(Language.EN, ZoneInfo("UTC"))

    async def test_second_successful_use_sends_only_a_private_followup(self) -> None:
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_not_awaited()
        await maybe_remind(self.request, self.context)
        self.request.followup.send.assert_awaited_once()
        call = self.request.followup.send.await_args
        if call is None:
            self.fail("Expected a reminder")
        self.assertTrue(call.kwargs["ephemeral"])
        view = call.kwargs["view"]
        self.assertIsInstance(view, ReminderView)
        if isinstance(view, ReminderView):
            view.stop()
        self.assertEqual(
            await self.repository.get(PreferenceScope.USER, 10), Preferences()
        )

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
