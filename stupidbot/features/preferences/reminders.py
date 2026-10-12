"""Optional private onboarding after successful personal profile commands."""

import logging
from datetime import UTC, datetime
from typing import Self, override

import discord

from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.pagination import CallbackButton
from stupidbot.features.preferences.models import PreferenceScope, ReminderReservation
from stupidbot.features.preferences.preferences_cog import open_settings
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import PresentationContext

logger = logging.getLogger(__name__)


class ReminderView(discord.ui.View):
    """Use a committed token so old controls cannot dismiss a newer reservation."""

    def __init__(
        self,
        repository: PreferencesRepository,
        reservation: ReminderReservation,
        context: PresentationContext,
    ) -> None:
        super().__init__(timeout=300)
        self.repository = repository
        self.reservation = reservation
        self.message: discord.WebhookMessage | None = None
        self.catalog = catalog("preferences", context.language)
        self.add_item(
            CallbackButton[ReminderView](
                self.open, label=self.catalog.pgettext("open_settings", "Настроить")
            )
        )
        self.add_item(
            CallbackButton[ReminderView](
                self.later, label=self.catalog.pgettext("later", "Позже")
            )
        )
        self.add_item(
            CallbackButton[ReminderView](
                self.never, label=self.catalog.pgettext("never", "Не напоминать")
            )
        )

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.reservation.user_id:
            context = await interaction_context(interaction, private=True)
            await interaction.response.send_message(
                catalog("preferences", context.language).pgettext(
                    "owner_only",
                    "Эта панель принадлежит другому пользователю. Откройте /settings.",
                ),
                ephemeral=True,
            )
            return False
        return await check_component_access(interaction)

    async def open(self, interaction: discord.Interaction) -> None:
        """Open a fresh private editor; only its Save completes setup."""
        await open_settings(interaction, self.repository, PreferenceScope.USER)

    async def later(self, interaction: discord.Interaction) -> None:
        """Keep the reservation's cooldown without changing editable preferences."""
        await self.answer(interaction, never=False)

    async def never(self, interaction: discord.Interaction) -> None:
        """Persist opt-out without changing any setting or its revision."""
        await self.answer(interaction, never=True)

    async def answer(self, interaction: discord.Interaction, *, never: bool) -> None:
        """Acknowledge before SQL; hide controls only after the token commits."""
        await interaction.response.defer()
        accepted = await self.repository.answer_prompt(self.reservation, never=never)
        if not accepted:
            text = self.catalog.pgettext(
                "reminder_expired", "Эта подсказка уже обработана."
            )
        elif never:
            text = self.catalog.pgettext(
                "reminder_disabled",
                "Больше не буду напоминать. Настройки доступны в /settings.",
            )
        else:
            text = self.catalog.pgettext(
                "reminder_later",
                "Хорошо. Настройки доступны в /settings в любое время.",
            )
        self.stop()
        await interaction.edit_original_response(content=text, view=None)

    @override
    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                logger.debug("Reminder timeout cleanup failed", exc_info=True)

    @override
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Self],
    ) -> None:
        await handle_app_command_error(interaction, error)


async def maybe_remind(
    interaction: discord.Interaction, context: PresentationContext
) -> None:
    """Isolate an optional reminder from a successfully delivered profile.

    A failed or uncertain send consumes the reservation. Retrying it could spam
    the user; it must not repeat or fail the already completed profile command.
    """
    repository: object = getattr(interaction.client, "preferences_repository", None)
    if not isinstance(repository, PreferencesRepository):
        return
    try:
        reservation = await repository.reserve_prompt_if_due(
            interaction.user.id, datetime.now(UTC)
        )
        if reservation is None:
            return
        messages = catalog("preferences", context.language)
        view = ReminderView(repository, reservation, context)
        try:
            view.message = await interaction.followup.send(
                messages.pgettext(
                    "reminder",
                    "Выберите язык и часовой пояс для личных ответов и голосового "
                    "профиля. Это можно сделать в /settings.",
                ),
                view=view,
                ephemeral=True,
                wait=True,
            )
        except Exception:
            view.stop()
            raise
    except Exception:
        logger.warning("Optional preference reminder failed")
        logger.debug("Preference reminder failure", exc_info=True)
