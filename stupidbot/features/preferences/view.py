"""Ephemeral settings editor with a frozen presentation context and CAS save."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

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


class LanguageSelect(discord.ui.Select["SettingsView"]):
    """Select only the language override; preserve a pending timezone edit."""

    def __init__(self, panel: "SettingsView") -> None:
        self.panel = panel
        super().__init__(
            options=[
                discord.SelectOption(
                    label=panel.catalog.pgettext("discord", "Как в Discord"),
                    value="discord",
                    default=panel.proposed.language is None,
                ),
                discord.SelectOption(
                    label="Русский",
                    value="ru",
                    default=panel.proposed.language is Language.RU,
                ),
                discord.SelectOption(
                    label="English",
                    value="en",
                    default=panel.proposed.language is Language.EN,
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


class TimezoneModal(discord.ui.Modal):
    """Validate an IANA identifier without changing the operational fallback."""

    def __init__(self, panel: "SettingsView") -> None:
        super().__init__(
            title=panel.catalog.pgettext("timezone", "Часовой пояс"), timeout=180
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
                text=panel.catalog.pgettext("zone_id", "IANA-зона"),
                description=panel.catalog.pgettext(
                    "zone_help",
                    "Например Europe/Moscow. Пустое поле — настройка установки.",
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
                    "Неизвестная IANA-зона. "
                    "Например: Europe/Moscow или Asia/Krasnoyarsk.",
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
        title = (
            self.catalog.pgettext("personal_title", "Личные настройки")
            if self.scope is PreferenceScope.USER
            else self.catalog.pgettext("server_title", "Настройки сервера")
        )
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"## {title}"),
                discord.ui.TextDisplay(
                    self.messages.preview(
                        language="Русский"
                        if preview.language is Language.RU
                        else "English",
                        timezone=preview.timezone.key,
                        local_time=datetime.now(UTC)
                        .astimezone(preview.timezone)
                        .strftime("%Y-%m-%d %H:%M"),
                    )
                ),
                discord.ui.ActionRow(LanguageSelect(self)),
                discord.ui.ActionRow(
                    CallbackButton[SettingsView](
                        self.edit_timezone,
                        label=self.catalog.pgettext("timezone", "Часовой пояс"),
                    ),
                    CallbackButton[SettingsView](
                        self.save,
                        label=self.catalog.pgettext("save", "Сохранить"),
                        style=discord.ButtonStyle.success,
                    ),
                    CallbackButton[SettingsView](
                        self.cancel, label=self.catalog.pgettext("cancel", "Отмена")
                    ),
                ),
            )
        )

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            context = await interaction_context(interaction, private=True)
            await interaction.response.send_message(
                catalog("preferences", context.language).pgettext(
                    "owner_only",
                    "Эта панель принадлежит другому пользователю. Откройте /settings.",
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
                    "Панель уже закрыта или сохраняется. Откройте настройки заново.",
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
                    "Для изменения настроек сервера нужно право «Управлять сервером».",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def edit_timezone(self, interaction: discord.Interaction) -> None:
        """Open a modal using the pending timezone, without committing anything."""
        await interaction.response.send_modal(TimezoneModal(self))

    async def save(self, interaction: discord.Interaction) -> None:
        """Acknowledge before SQL and confirm only after the transaction commits."""
        self.saving = True
        proposed = self.proposed
        await interaction.response.defer()
        interaction.extras["stupidbot.preferences.failed"] = True
        try:
            saved = await self.repository.save(self.scope, self.subject_id, proposed)
        except PreferenceConflictError as error:
            current = error.current
            await self.finish(
                interaction,
                self.messages.conflict(
                    language=current.language.value
                    if current.language
                    else self.catalog.pgettext("discord", "Как в Discord"),
                    timezone=current.timezone or self.fallback_timezone.key,
                ),
            )
        else:
            interaction.extras["stupidbot.preferences.failed"] = False
            self.proposed = saved
            await self.finish(
                interaction, self.catalog.pgettext("saved", "Настройки сохранены.")
            )
        finally:
            self.saving = False

    async def cancel(self, interaction: discord.Interaction) -> None:
        """Close the editor without saving pending overrides."""
        await interaction.response.defer()
        await self.finish(
            interaction, self.catalog.pgettext("cancelled", "Изменения отменены.")
        )

    async def finish(self, interaction: discord.Interaction, text: str) -> None:
        """Keep the response in Components V2 after removing interactive controls."""
        self.closed = True
        self.clear_items()
        self.add_item(discord.ui.TextDisplay(text))
        self.stop()
        await interaction.edit_original_response(view=self)

    @override
    async def on_timeout(self) -> None:
        if self.saving:
            return
        self.closed = True
        self.clear_items()
        self.add_item(
            discord.ui.TextDisplay(
                self.catalog.pgettext(
                    "expired",
                    "Время редактирования истекло. Откройте настройки заново.",
                )
            )
        )
        self.stop()
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
