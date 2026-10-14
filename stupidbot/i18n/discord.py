"""Resolve response audiences once and retain a safe context after storage failure."""

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from stupidbot import settings
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import PresentationContext, resolve_context

logger = logging.getLogger(__name__)


def fallback_timezone() -> ZoneInfo:
    """Preserve the existing installation's effective VOICE_PROFILE_TIMEZONE."""
    try:
        return ZoneInfo(settings.VOICE_PROFILE_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Invalid voice profile timezone; using UTC")
        return ZoneInfo("UTC")


async def interaction_context(
    interaction: discord.Interaction, *, private: bool
) -> PresentationContext:
    """Use user overrides for private/DM output and guild overrides for public output.

    Install a fallback before the first database await. An error response on the
    same interaction never retries preferences after a failed database lookup.
    Panels retain their returned context rather than resolving on each edit.
    """
    personal = private or interaction.guild_id is None
    key = "stupidbot.context.private" if personal else "stupidbot.context.public"
    cached: object = interaction.extras.get(key)
    if isinstance(cached, PresentationContext):
        return cached
    locale = interaction.locale if personal else interaction.guild_locale
    locale_value = locale.value if isinstance(locale, discord.Locale) else None
    fallback = resolve_context(
        language=None,
        timezone=None,
        locale=locale_value,
        fallback_timezone=fallback_timezone(),
    )
    interaction.extras[key] = fallback
    if interaction.extras.get("stupidbot.preferences.failed"):
        return fallback
    repository: object = getattr(interaction.client, "preferences_repository", None)
    if not isinstance(repository, PreferencesRepository):
        return fallback
    if personal:
        scope = PreferenceScope.USER
        subject_id = interaction.user.id
    else:
        scope = PreferenceScope.GUILD
        subject_id = interaction.guild_id
    if subject_id is None:
        raise ValueError("Guild preferences require a guild")
    interaction.extras["stupidbot.preferences.failed"] = True
    preferences = await repository.get(scope, subject_id)
    resolved = resolve_context(
        language=preferences.language,
        timezone=preferences.timezone,
        locale=locale_value,
        fallback_timezone=fallback.timezone,
    )
    interaction.extras["stupidbot.preferences.failed"] = False
    interaction.extras[key] = resolved
    if personal:
        interaction.extras["stupidbot.preferences.user"] = preferences
    return resolved


async def profile_context(interaction: discord.Interaction) -> PresentationContext:
    """Use personal language and inherit only an unset timezone from the guild.

    Reuse the interaction's preference snapshot, so explicit user overrides need
    no guild query. Both lookups retain the ordinary error-response fallback.
    """
    personal = await interaction_context(interaction, private=True)
    preferences: object = interaction.extras.get("stupidbot.preferences.user")
    if (
        isinstance(preferences, Preferences)
        and preferences.timezone is None
        and interaction.guild_id is not None
    ):
        server = await interaction_context(interaction, private=False)
        return PresentationContext(personal.language, server.timezone)
    return personal


async def error_context(interaction: discord.Interaction) -> PresentationContext:
    """Keep storage failure from preventing the error response itself."""
    try:
        return await interaction_context(interaction, private=True)
    except Exception:
        logger.warning("Preferences unavailable while reporting an interaction error")
        logger.debug("Preferences lookup failed", exc_info=True)
        # The first attempt installed a fallback and prohibited a database retry.
        return await interaction_context(interaction, private=True)


async def guild_context(client: discord.Client, guild_id: int) -> PresentationContext:
    """Resolve public output without letting optional settings prevent delivery.

    Failed reads or invalid stored overrides fall back to the guild locale and
    installation timezone. Log the failure once without retrying storage; caller
    cancellation still propagates so shutdown can stop background work.
    """
    guild = client.get_guild(guild_id)
    locale = guild.preferred_locale if guild is not None else None
    locale_value = locale.value if isinstance(locale, discord.Locale) else None
    fallback = resolve_context(
        language=None,
        timezone=None,
        locale=locale_value,
        fallback_timezone=fallback_timezone(),
    )
    repository: object = getattr(client, "preferences_repository", None)
    if not isinstance(repository, PreferencesRepository):
        return fallback
    try:
        preferences = await repository.get(PreferenceScope.GUILD, guild_id)
        return resolve_context(
            language=preferences.language,
            timezone=preferences.timezone,
            locale=locale_value,
            fallback_timezone=fallback.timezone,
        )
    except Exception:
        logger.warning("Guild preferences unavailable; using presentation defaults")
        logger.debug("Guild preferences lookup failed", exc_info=True)
        return fallback


async def message_context(
    client: discord.Client, message: discord.Message
) -> PresentationContext:
    """Resolve public guild replies or personal DM replies without a Discord locale.

    Gateway messages do not carry the author's locale. DM replies therefore use
    the personal override and then Russian; guild replies use the guild locale.
    """
    if message.guild is not None:
        return await guild_context(client, message.guild.id)
    repository: object = getattr(client, "preferences_repository", None)
    preferences = (
        await repository.get(PreferenceScope.USER, message.author.id)
        if isinstance(repository, PreferencesRepository)
        else None
    )
    return resolve_context(
        language=preferences.language if preferences is not None else None,
        timezone=preferences.timezone if preferences is not None else None,
        locale=None,
        fallback_timezone=fallback_timezone(),
    )
