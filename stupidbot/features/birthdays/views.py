"""Versioned confirmation before removing a saved birthday."""

import logging
from typing import Self

import discord
from discord import Interaction
from discord.ui import Button

from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import PresentationContext

logger = logging.getLogger(__name__)


class ConfirmDeleteView(discord.ui.View):
    """Confirmation view for birthday deletion."""

    def __init__(
        self,
        user_id: int,
        guild_id: int,
        manager: BirthdayRepository,
        expected_version: int,
        *,
        context: PresentationContext,
    ) -> None:
        super().__init__(timeout=30)
        self.context = context
        self.messages = catalog("birthdays", context.language)
        self.confirm.label = self.messages.pgettext("ui", "Да")
        self.cancel.label = self.messages.pgettext("ui", "Нет")
        self.user_id = user_id
        self.expected_version = expected_version
        self.guild_id = guild_id
        self.manager = manager

    @discord.ui.button(label=pgettext_noop("ui", "Да"), style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, _: Button[Self]) -> None:
        if interaction.user.id != self.user_id:
            context = await interaction_context(interaction, private=True)
            messages = catalog("birthdays", context.language)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description=messages.pgettext(
                    "ui", "Вы не можете выполнить это действие"
                ),
                ephemeral=True,
            )
            return

        if not await check_component_access(interaction):
            return
        try:
            guild_exists, cleared = await self.manager.clear_user_birthday(
                self.guild_id, self.user_id, expected_version=self.expected_version
            )
        except Exception:
            logger.exception(
                "Failed to save birthday removal for user %s", self.user_id
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title=self.messages.pgettext("ui", "Ошибка"),
                description=self.messages.pgettext(
                    "ui", "Произошла ошибка при удалении дня рождения"
                ),
                ephemeral=True,
            )
            return
        if not guild_exists:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description=self.messages.pgettext(
                    "ui", "Конфигурация сервера не найдена"
                ),
                ephemeral=True,
            )
            return
        if not cleared:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=(
                    self.messages.pgettext(
                        "ui",
                        "Дата уже изменена или удалена. Откройте подтверждение заново.",
                    )
                ),
                ephemeral=True,
            )
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=self.messages.pgettext("ui", "Ваш день рождения удалён"),
            ephemeral=True,
        )

    @discord.ui.button(label=pgettext_noop("ui", "Нет"), style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, _: Button[Self]) -> None:
        if interaction.user.id != self.user_id:
            context = await interaction_context(interaction, private=True)
            messages = catalog("birthdays", context.language)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description=messages.pgettext(
                    "ui", "Вы не можете выполнить это действие."
                ),
                ephemeral=True,
            )
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description=self.messages.pgettext("ui", "Отменено"),
            ephemeral=True,
        )
