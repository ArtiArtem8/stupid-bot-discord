"""Resolve each audience without leaking user settings into public messages."""

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import Language, resolve_context, resolve_language
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
