"""Ephemeral settings editor with a frozen presentation context and CAS save."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from stupidbot.common.timezones import timezone_label
from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.pagination import CallbackButton
from stupidbot.features.preferences.messages import PreferenceMessages
from stupidbot.features.preferences.models import (
    PreferenceConflictError,
    Preferences,
    PreferenceScope,
)
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import Language, PresentationContext, resolve_context

logger = logging.getLogger(__name__)

# Keep one of Discord's 25 select options for arbitrary regional timezones.
_TIMEZONE_PRESETS = tuple(
    "UTC" if offset == 0 else f"Etc/GMT{-offset:+d}" for offset in range(-11, 13)
)


class LanguageSelect(discord.ui.Select["SettingsView"]):
    """Select only the language override; preserve a pending timezone edit."""

    def __init__(self, panel: "SettingsView") -> None:
        self.panel = panel
        super().__init__(
            options=[
                discord.SelectOption(
                    label=panel.catalog.pgettext("discord", "As in Discord"),
                    value="discord",
                    default=panel.proposed.language is None,
                ),
                *(
                    discord.SelectOption(
                        label=language.native_name,
                        value=language.value,
                        default=panel.proposed.language is language,
                    )
                    for language in Language
                ),
            ]
        )

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        self.panel.proposed = replace(
            self.panel.proposed,
            language=None if value == "discord" else Language(value),
        )
        self.panel.rebuild()
        await interaction.response.edit_message(view=self.panel)


class TimezoneSelect(discord.ui.Select["SettingsView"]):
    """Choose a preset or open a modal for an arbitrary IANA timezone."""

    def __init__(self, panel: "SettingsView") -> None:
        self.panel = panel
        current = panel.proposed.timezone
        custom = current is not None and current not in _TIMEZONE_PRESETS
        super().__init__(
            placeholder=(
                panel.catalog.pgettext("zone_inherit", "Default timezone")
                if current is None
                else panel.catalog.pgettext("timezone", "Timezone")
            ),
            options=[
                *(
                    discord.SelectOption(
                        label=timezone_label(zone),
                        value=zone,
                        description=panel.catalog.pgettext(
                            "fixed_offset", "Fixed offset, no daylight saving time"
                        ),
                        default=current == zone,
                    )
                    for zone in _TIMEZONE_PRESETS
                ),
                discord.SelectOption(
                    label=panel.catalog.pgettext("zone_other", "Other…"),
                    value="other",
                    description=current if custom else None,
                    default=custom,
                ),
            ],
        )

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        if value == "other":
            await interaction.response.send_modal(TimezoneModal(self.panel))
            return
        self.panel.proposed = replace(self.panel.proposed, timezone=value)
        self.panel.rebuild()
        await interaction.response.edit_message(view=self.panel)


class TimezoneModal(discord.ui.Modal):
    """Validate an IANA identifier without changing the operational fallback."""

    def __init__(self, panel: "SettingsView") -> None:
        super().__init__(
            title=panel.catalog.pgettext("timezone", "Timezone"), timeout=180
        )
        self.panel = panel
        self.zone: discord.ui.TextInput[TimezoneModal] = discord.ui.TextInput(
            default=panel.proposed.timezone or "",
            required=False,
            max_length=100,
            placeholder="Europe/Moscow",
        )
        self.add_item(
            discord.ui.Label(
                text=panel.catalog.pgettext("zone_id", "IANA timezone"),
                description=panel.catalog.pgettext(
                    "zone_help",
                    "For example Europe/Moscow. Leave blank to use the default "
                    "timezone.",
                ),
                component=self.zone,
            )
        )

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        value = self.zone.value.strip() or None
        try:
            proposed = replace(self.panel.proposed, timezone=value)
        except (ZoneInfoNotFoundError, ValueError):
            await interaction.response.send_message(
                self.panel.catalog.pgettext(
                    "invalid_zone",
                    "Unknown IANA timezone. Examples: Europe/Moscow or UTC.",
                ),
                ephemeral=True,
            )
            return
        self.panel.proposed = proposed
        self.panel.rebuild()
        await interaction.response.edit_message(view=self.panel)

    @override
    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await handle_app_command_error(interaction, error)


class SettingsView(discord.ui.LayoutView):
    """Own one finite editor; changes become visible only after a successful commit."""

    def __init__(
        self,
        *,
        owner_id: int,
        scope: PreferenceScope,
        subject_id: int,
        repository: PreferencesRepository,
        initial: Preferences,
        context: PresentationContext,
        audience_locale: str | None,
        fallback_timezone: ZoneInfo,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.scope = scope
        self.subject_id = subject_id
        self.repository = repository
        self.proposed = initial
        self.context = context
        self.audience_locale = audience_locale
        self.fallback_timezone = fallback_timezone
        self.catalog = catalog("preferences", context.language)
        self.messages = PreferenceMessages(self.catalog)
        self.message: discord.InteractionMessage | None = None
        self.closed = False
        self.saving = False
        self.rebuild()

    def rebuild(self) -> None:
        """Render pending values in the panel's original language and timezone."""
        preview = resolve_context(
            language=self.proposed.language,
            timezone=self.proposed.timezone,
            locale=self.audience_locale,
            fallback_timezone=self.fallback_timezone,
        )
        language = preview.language.native_name
        if self.scope is PreferenceScope.USER:
            title = self.catalog.pgettext("personal_title", "Your settings")
            scope_description = self.catalog.pgettext(
                "personal_scope",
                "Your language for personal replies and all your profiles, including "
                "public ones, across all servers.\n"
                "Timezone changes dates and charts, not XP or time in voice. "
                "If unset, profiles use the server timezone.",
            )
        else:
            title = self.catalog.pgettext("server_title", "Server settings")
            scope_description = self.catalog.pgettext(
                "server_scope",
                "Language for shared messages. Timezone for profiles without a "
                "personal timezone. Birthday schedules stay unchanged.",
            )
        content = discord.ui.Container(
            discord.ui.TextDisplay(f"## {title}"),
            discord.ui.TextDisplay(scope_description),
            discord.ui.TextDisplay(
                self.messages.preview(
                    language=language,
                    timezone=timezone_label(preview.timezone.key),
                    local_time=datetime.now(UTC)
                    .astimezone(preview.timezone)
                    .strftime("%Y-%m-%d %H:%M"),
                )
            ),
            discord.ui.ActionRow(LanguageSelect(self)),
            discord.ui.ActionRow(TimezoneSelect(self)),
        )
        content.add_item(
            discord.ui.ActionRow(
                CallbackButton[SettingsView](
                    self.save,
                    label=self.catalog.pgettext("save", "Save"),
                    style=discord.ButtonStyle.success,
                ),
                CallbackButton[SettingsView](
                    self.cancel, label=self.catalog.pgettext("cancel", "Cancel")
                ),
                CallbackButton[SettingsView](
                    self.reset,
                    label=self.catalog.pgettext("reset", "Reset settings"),
                ),
            )
        )
        self.clear_items()
        self.add_item(content)

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            context = await interaction_context(interaction, private=True)
            await interaction.response.send_message(
                catalog("preferences", context.language).pgettext(
                    "owner_only",
                    "This panel belongs to someone else. Open /customize-me.",
                ),
                ephemeral=True,
            )
            return False
        if not await check_component_access(interaction):
            return False
        if self.closed or self.saving:
            await interaction.response.send_message(
                self.catalog.pgettext(
                    "closed",
                    "This panel is closed or saving. Open settings again.",
                ),
                ephemeral=True,
            )
            return False
        if self.scope is PreferenceScope.GUILD and (
            interaction.guild_id != self.subject_id
            or not interaction.permissions.manage_guild
        ):
            await interaction.response.send_message(
                self.catalog.pgettext(
                    "permission",
                    "You need Manage Server permission to change server settings.",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def reset(self, interaction: discord.Interaction) -> None:
        """Stage default overrides, preserving the revision until Save or Cancel."""
        self.proposed = replace(self.proposed, language=None, timezone=None)
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def save(self, interaction: discord.Interaction) -> None:
        """Acknowledge before SQL and confirm only after the transaction commits."""
        self.saving = True
        proposed = self.proposed
        try:
            await interaction.response.defer()
            interaction.extras["stupidbot.preferences.failed"] = True
            saved = await self.repository.save(self.scope, self.subject_id, proposed)
        except PreferenceConflictError as error:
            current = error.current
            text = self.messages.conflict(
                language=current.language.native_name
                if current.language
                else self.catalog.pgettext("discord", "As in Discord"),
                timezone=timezone_label(current.timezone or self.fallback_timezone.key),
            )
        else:
            interaction.extras["stupidbot.preferences.failed"] = False
            self.proposed = saved
            text = self.catalog.pgettext("saved", "Settings saved.")
        finally:
            self.saving = False
        self._finish(text)
        await interaction.edit_original_response(view=self)

    async def cancel(self, interaction: discord.Interaction) -> None:
        """Close the editor without saving pending overrides."""
        self._finish(self.catalog.pgettext("cancelled", "Changes cancelled."))
        await interaction.response.edit_message(view=self)

    def _finish(self, text: str) -> None:
        # Reject concurrent interactions before waiting for Discord to update.
        self.closed = True
        self.clear_items()
        self.add_item(discord.ui.TextDisplay(text))
        self.stop()

    @override
    async def on_timeout(self) -> None:
        if self.saving:
            return
        self._finish(
            self.catalog.pgettext(
                "expired",
                "This editor has expired. Open settings again.",
            )
        )
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Settings timeout cleanup failed", exc_info=True)

    @override
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item["SettingsView"],
    ) -> None:
        await handle_app_command_error(interaction, error)
