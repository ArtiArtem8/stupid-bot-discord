"""Localize Discord command metadata using the same checked owner catalogs."""

from typing import override

import discord
from discord import app_commands

from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import resolve_language


class CommandTranslator(app_commands.Translator):
    """Translate command metadata for Discord's locale."""

    @override
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        domain: object = string.extras.get("domain")
        if not isinstance(domain, str):
            return None
        return catalog(domain, resolve_language(None, locale.value)).pgettext(
            "command", string.message
        )
