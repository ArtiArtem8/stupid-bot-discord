"""Public settings commands; all editors are ephemeral."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.features.preferences.models import PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.features.preferences.view import SettingsView
from stupidbot.i18n.discord import fallback_timezone, interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot


class PreferencesCog(BaseCog):
    """Apply ordinary access checks and current server-management permissions."""

    def __init__(self, bot: commands.Bot, repository: PreferencesRepository) -> None:
        super().__init__(bot)
        self.repository = repository

    @app_commands.command(
        name="customize-me",
        description=app_commands.locale_str(
            "Your language and timezone for replies and your profile",
            domain="preferences",
        ),
    )
    async def customize_me(self, interaction: discord.Interaction) -> None:
        await open_settings(interaction, self.repository, PreferenceScope.USER)

    @app_commands.command(
        name="settings-server",
        description=app_commands.locale_str(
            "Language and timezone for public server messages", domain="preferences"
        ),
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings_server(self, interaction: discord.Interaction) -> None:
        await open_settings(interaction, self.repository, PreferenceScope.GUILD)


async def open_settings(
    interaction: discord.Interaction,
    repository: PreferencesRepository,
    scope: PreferenceScope,
) -> None:
    """Freeze the viewer's context, then read the selected settings scope."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    context = await interaction_context(interaction, private=True)
    subject_id = (
        interaction.user.id if scope is PreferenceScope.USER else interaction.guild_id
    )
    if subject_id is None:
        raise app_commands.NoPrivateMessage()
    initial = await repository.get(scope, subject_id)
    locale = (
        interaction.locale
        if scope is PreferenceScope.USER
        else interaction.guild_locale
    )
    view = SettingsView(
        owner_id=interaction.user.id,
        scope=scope,
        subject_id=subject_id,
        repository=repository,
        initial=initial,
        context=context,
        audience_locale=locale.value if locale is not None else None,
        fallback_timezone=fallback_timezone(),
    )
    view.message = await interaction.edit_original_response(view=view)


async def setup(bot: "StupidBot") -> None:
    """Register preferences using the application's shared database owner."""
    await bot.add_cog(PreferencesCog(bot, bot.preferences_repository))
