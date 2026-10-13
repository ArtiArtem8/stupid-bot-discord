"""Exercise Discord metadata translation with the shipped language catalogs."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

import discord
from discord import app_commands

from stupidbot.i18n.catalog import catalogs
from stupidbot.i18n.commands import CommandTranslator
from stupidbot.i18n.policy import Language


class CommandTranslationTests(IsolatedAsyncioTestCase):
    async def test_korean_metadata_uses_its_catalog_within_discord_limits(self) -> None:
        translator = CommandTranslator()
        context = MagicMock(spec=app_commands.TranslationContext)
        translated_count = 0
        for (domain, language), messages in catalogs().items():
            if language is not Language.KO:
                continue
            for key in messages.keys:
                message_context, source = key.split("\x04", 1)
                if message_context != "command":
                    continue
                with self.subTest(domain=domain, source=source):
                    translated = await translator.translate(
                        app_commands.locale_str(source, domain=domain),
                        discord.Locale.korean,
                        context,
                    )
                    self.assertIsNotNone(translated)
                    if translated is None:
                        self.fail("Command metadata must have a translation")
                    self.assertEqual(translated, messages.pgettext("command", source))
                    self.assertNotEqual(translated, source)
                    self.assertLessEqual(len(translated), 100)
                    translated_count += 1
        self.assertGreater(translated_count, 0)
