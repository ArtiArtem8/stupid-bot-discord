"""Verify edits and reminders against migrated disposable SQLite databases."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import override
from unittest import IsolatedAsyncioTestCase
from zoneinfo import ZoneInfoNotFoundError

from stupidbot.features.preferences.models import (
    PreferenceConflictError,
    Preferences,
    PreferenceScope,
)
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import Language
from tests.storage.fixtures import temporary_database


class PreferencesRepositoryTests(IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repository = PreferencesRepository(self.database)
        self.scope = PreferenceScope.USER
        self.now = datetime(2026, 10, 12, tzinfo=UTC)

    async def test_save_reset_language_and_noop_preserve_timezone(self) -> None:
        initial = await self.repository.get(self.scope, 10)
        self.assertEqual(initial, Preferences())
        first = await self.repository.save(
            self.scope, 10, Preferences(Language.EN, "Asia/Krasnoyarsk")
        )
        self.assertEqual(first.revision, 1)
        reset = await self.repository.save(
            self.scope, 10, Preferences(None, first.timezone, first.revision)
        )
        self.assertEqual(reset, Preferences(None, "Asia/Krasnoyarsk", 2))
        self.assertEqual(await self.repository.save(self.scope, 10, reset), reset)
        self.assertEqual(await self.repository.get(self.scope, 10), reset)
        self.assertEqual(
            await self.repository.get(PreferenceScope.GUILD, 10), Preferences()
        )

    async def test_two_editors_have_one_committed_winner(self) -> None:
        async def save(language: Language) -> Preferences | PreferenceConflictError:
            try:
                return await self.repository.save(self.scope, 10, Preferences(language))
            except PreferenceConflictError as error:
                return error

        results = await asyncio.gather(save(Language.RU), save(Language.EN))
        saved = [result for result in results if isinstance(result, Preferences)]
        conflicts = [
            result for result in results if isinstance(result, PreferenceConflictError)
        ]
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].current, saved[0])
        self.assertEqual(await self.repository.get(self.scope, 10), saved[0])

    async def test_scopes_are_independent_and_survive_new_repository(self) -> None:
        await self.repository.save(self.scope, 10, Preferences(Language.EN))
        await self.repository.save(PreferenceScope.GUILD, 10, Preferences(Language.RU))
        reopened = PreferencesRepository(self.database)
        self.assertEqual((await reopened.get(self.scope, 10)).language, Language.EN)
        self.assertEqual(
            (await reopened.get(PreferenceScope.GUILD, 10)).language, Language.RU
        )

    async def test_concurrent_reminders_do_not_conflict_with_open_editor(self) -> None:
        self.assertIsNone(await self.repository.reserve_prompt_if_due(10, self.now))
        results = await asyncio.gather(
            *(self.repository.reserve_prompt_if_due(10, self.now) for _ in range(6))
        )
        reservations = [value for value in results if value is not None]
        self.assertEqual(len(reservations), 1)
        self.assertEqual(await self.repository.get(self.scope, 10), Preferences())
        self.assertTrue(
            await self.repository.answer_prompt(reservations[0], never=False)
        )
        self.assertFalse(
            await self.repository.answer_prompt(reservations[0], never=True)
        )
        self.assertEqual(
            (
                await self.repository.save(self.scope, 10, Preferences(Language.EN))
            ).revision,
            1,
        )

    async def test_cooldown_and_maximum_survive_new_repository(self) -> None:
        await self.repository.reserve_prompt_if_due(10, self.now)
        first = await self.repository.reserve_prompt_if_due(10, self.now)
        self.assertIsNotNone(first)
        reopened = PreferencesRepository(self.database)
        self.assertIsNone(
            await reopened.reserve_prompt_if_due(10, self.now + timedelta(days=13))
        )
        self.assertIsNotNone(
            await reopened.reserve_prompt_if_due(10, self.now + timedelta(days=14))
        )
        self.assertIsNone(
            await reopened.reserve_prompt_if_due(10, self.now + timedelta(days=90))
        )

    async def test_never_suppresses_future_prompts(self) -> None:
        await self.repository.reserve_prompt_if_due(10, self.now)
        reservation = await self.repository.reserve_prompt_if_due(10, self.now)
        self.assertIsNotNone(reservation)
        if reservation is None:
            self.fail("Second use must reserve a reminder")
        self.assertTrue(await self.repository.answer_prompt(reservation, never=True))
        self.assertIsNone(
            await self.repository.reserve_prompt_if_due(
                10, self.now + timedelta(days=90)
            )
        )

    async def test_invalid_zone_never_reaches_storage(self) -> None:
        with self.assertRaises(ZoneInfoNotFoundError):
            Preferences(timezone="Not/AZone")
        self.assertEqual(await self.repository.get(self.scope, 10), Preferences())
