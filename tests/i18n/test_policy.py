"""Resolve shared responses and personal profile presentation."""

import asyncio
from typing import override
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot import settings
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n import discord as discord_i18n
from stupidbot.i18n.discord import (
    error_context,
    guild_context,
    interaction_context,
    profile_context,
)
from stupidbot.i18n.policy import Language, resolve_context, resolve_language
from stupidbot.storage.schema import guild_preferences
from tests.features.preferences.fixtures import interaction
from tests.storage.fixtures import temporary_database


class PolicyTests(TestCase):
    def test_override_discord_locale_and_fallback_priority(self) -> None:
        for locale, expected in (
            ("en-US", Language.EN),
            ("en-GB", Language.EN),
            ("ru", Language.RU),
            ("de", Language.RU),
            (None, Language.RU),
        ):
            with self.subTest(locale=locale):
                self.assertEqual(resolve_language(None, locale), expected)
                self.assertEqual(resolve_language(Language.RU, locale), Language.RU)
                self.assertEqual(resolve_language(Language.EN, locale), Language.EN)

    def test_absent_preferences_preserve_installation_timezone(self) -> None:
        context = resolve_context(
            language=None,
            timezone=None,
            locale=None,
            fallback_timezone=ZoneInfo("Asia/Krasnoyarsk"),
        )
        self.assertEqual(context.timezone.key, "Asia/Krasnoyarsk")


class InteractionPolicyTests(IsolatedAsyncioTestCase):
    async def test_private_and_public_contexts_use_separate_owners(self) -> None:
        _, database = await temporary_database(self)
        repository = PreferencesRepository(database)
        await repository.save(
            PreferenceScope.USER, 10, Preferences(Language.EN, "Europe/London")
        )
        await repository.save(
            PreferenceScope.GUILD, 20, Preferences(Language.RU, "Asia/Krasnoyarsk")
        )
        request = interaction()
        request.client.preferences_repository = repository
        private = await interaction_context(request, private=True)
        public = await interaction_context(request, private=False)
        self.assertEqual(
            (private.language, private.timezone.key), (Language.EN, "Europe/London")
        )
        self.assertEqual(
            (public.language, public.timezone.key), (Language.RU, "Asia/Krasnoyarsk")
        )

    async def test_storage_failure_retains_safe_context_without_second_lookup(
        self,
    ) -> None:
        _, database = await temporary_database(self)
        repository = PreferencesRepository(database)
        request = interaction()
        request.client.preferences_repository = repository
        with patch.object(
            repository, "get", side_effect=OSError("unavailable")
        ) as read:
            with self.assertRaises(OSError):
                await interaction_context(request, private=False)
            context = await interaction_context(request, private=True)
        read.assert_awaited_once()
        self.assertEqual(context.language, Language.EN)


class ProfilePolicyTests(IsolatedAsyncioTestCase):
    async def test_timezone_inherits_without_changing_personal_language(self) -> None:
        _, database = await temporary_database(self)
        repository = PreferencesRepository(database)
        for index, (personal_zone, server_zone, expected) in enumerate(
            (
                ("Etc/GMT-3", "Etc/GMT+5", "Etc/GMT-3"),
                (None, "Europe/London", "Europe/London"),
                (None, None, "UTC"),
            )
        ):
            with self.subTest(personal=personal_zone, server=server_zone):
                request = interaction(100 + index)
                request.guild_id = 200 + index
                request.client.preferences_repository = repository
                await repository.save(
                    PreferenceScope.USER,
                    request.user.id,
                    Preferences(Language.EN, personal_zone),
                )
                await repository.save(
                    PreferenceScope.GUILD,
                    request.guild_id,
                    Preferences(Language.RU, server_zone),
                )
                with (
                    patch.object(settings, "VOICE_PROFILE_TIMEZONE", "UTC"),
                    patch.object(repository, "get", wraps=repository.get) as read,
                ):
                    context = await profile_context(request)
                    self.assertEqual(context.language, Language.EN)
                    self.assertEqual(context.timezone.key, expected)
                    self.assertEqual(await profile_context(request), context)
                self.assertEqual(read.await_count, 1 if personal_zone else 2)

    async def test_inherited_zone_failure_does_not_retry_for_error_feedback(
        self,
    ) -> None:
        _, database = await temporary_database(self)
        repository = PreferencesRepository(database)
        request = interaction()
        request.client.preferences_repository = repository
        with (
            patch.object(settings, "VOICE_PROFILE_TIMEZONE", "UTC"),
            patch.object(
                repository,
                "get",
                side_effect=[Preferences(Language.EN), OSError("unavailable")],
            ) as read,
        ):
            with self.assertRaises(OSError):
                await profile_context(request)
            context = await error_context(request)
        self.assertEqual(read.await_count, 2)
        self.assertEqual(context.language, Language.EN)
        self.assertEqual(context.timezone.key, "UTC")


class GuildPolicyTests(IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repository = PreferencesRepository(self.database)
        self.client = MagicMock(spec=discord.Client)
        self.client.preferences_repository = self.repository
        self.client.get_guild.return_value = MagicMock(
            preferred_locale=discord.Locale.american_english
        )

    async def test_guild_overrides_still_take_priority(self) -> None:
        await self.repository.save(
            PreferenceScope.GUILD, 20, Preferences(Language.RU, "Asia/Krasnoyarsk")
        )
        context = await guild_context(self.client, 20)
        self.assertEqual(context.language, Language.RU)
        self.assertEqual(context.timezone.key, "Asia/Krasnoyarsk")

    async def test_storage_failure_uses_locale_and_installation_zone_once(self) -> None:
        for locale, language in (
            (discord.Locale.american_english, Language.EN),
            (discord.Locale.russian, Language.RU),
            (discord.Locale.german, Language.RU),
            (None, Language.RU),
        ):
            with self.subTest(locale=locale):
                self.client.get_guild.return_value = (
                    MagicMock(preferred_locale=locale) if locale is not None else None
                )
                with (
                    patch.object(
                        settings, "VOICE_PROFILE_TIMEZONE", "Asia/Krasnoyarsk"
                    ),
                    patch.object(
                        self.repository, "get", side_effect=OSError("private details")
                    ) as read,
                    self.assertLogs(discord_i18n.logger, level="WARNING") as logs,
                ):
                    context = await guild_context(self.client, 20)
                read.assert_awaited_once_with(PreferenceScope.GUILD, 20)
                self.assertEqual(context.language, language)
                self.assertEqual(context.timezone.key, "Asia/Krasnoyarsk")
                self.assertEqual(len(logs.records), 1)
                self.assertNotIn("private details", logs.output[0])
                self.assertIsNone(logs.records[0].exc_info)

    async def test_invalid_stored_timezone_uses_safe_fallback(self) -> None:
        await self.repository.save(
            PreferenceScope.GUILD, 20, Preferences(Language.RU, "UTC")
        )
        async with self.database.transaction() as connection:
            await connection.execute(
                guild_preferences.update()
                .where(guild_preferences.c.subject_id == 20)
                .values(timezone="Invalid/Timezone")
            )
        with (
            patch.object(settings, "VOICE_PROFILE_TIMEZONE", "Europe/Moscow"),
            self.assertLogs(discord_i18n.logger, level="WARNING"),
        ):
            context = await guild_context(self.client, 20)
        self.assertEqual(context.language, Language.EN)
        self.assertEqual(context.timezone.key, "Europe/Moscow")

    async def test_lookup_cancellation_propagates(self) -> None:
        with patch.object(
            self.repository, "get", side_effect=asyncio.CancelledError()
        ) as read:
            with self.assertRaises(asyncio.CancelledError):
                await guild_context(self.client, 20)
        read.assert_awaited_once()
