"""Resolve response audiences once and retain a safe context after storage failure."""

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from stupidbot import settings
from stupidbot.features.preferences.models import PreferenceScope
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
    fallback = resolve_context(
        language=None,
        timezone=None,
        locale=locale.value if isinstance(locale, discord.Locale) else None,
        fallback_timezone=fallback_timezone(),
    )
    interaction.extras[key] = fallback
    if interaction.extras.get("stupidbot.preferences.failed"):
        return fallback
    repository: object = getattr(interaction.client, "preferences_repository", None)
    if not isinstance(repository, PreferencesRepository):
        return fallback
    scope = PreferenceScope.USER if personal else PreferenceScope.GUILD
    subject_id = interaction.user.id if personal else interaction.guild_id
    if subject_id is None:
        raise ValueError("Guild preferences require a guild")
    interaction.extras["stupidbot.preferences.failed"] = True
    preferences = await repository.get(scope, subject_id)
    resolved = resolve_context(
        language=preferences.language,
        timezone=preferences.timezone,
        locale=locale.value if isinstance(locale, discord.Locale) else None,
        fallback_timezone=fallback.timezone,
    )
    interaction.extras["stupidbot.preferences.failed"] = False
    interaction.extras[key] = resolved
    return resolved


async def error_context(interaction: discord.Interaction) -> PresentationContext:
    """Keep storage failure from preventing the error response itself."""
    try:
        return await interaction_context(interaction, private=True)
    except Exception:
        logger.warning("Preferences unavailable while reporting an interaction error")
        logger.debug("Preferences lookup failed", exc_info=True)
        # The first attempt installed a fallback and prohibited a database retry.
        return await interaction_context(interaction, private=True)
