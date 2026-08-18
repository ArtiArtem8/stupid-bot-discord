import logging
from datetime import timedelta
from typing import override

import discord
from discord import Interaction, app_commands
from discord.utils import utcnow

from api.exceptions import StupidBotError
from framework import BlockedUserError, FeedbackType, FeedbackUI, NoGuildError

logger = logging.getLogger(__name__)


class CustomErrorCommandTree(app_commands.CommandTree[discord.Client]):
    @override
    async def on_error(  # ty: ignore[invalid-method-override] matches discord.py CommandTree.on_error; basedpyright accepts this override.
        self,
        interaction: Interaction[discord.Client],
        error: app_commands.AppCommandError,
        /,
    ) -> None:
        """Event handler for when an app command error occurs."""
        if isinstance(error, BlockedUserError):
            await interaction.response.send_message(
                "⛔ Доступ к командам запрещён.", ephemeral=True
            )
            return
        if isinstance(error, NoGuildError):
            await interaction.response.send_message(
                "Команда может быть использована только на сервере",
                ephemeral=True,
                silent=True,
            )
            return
        if isinstance(error, app_commands.CommandOnCooldown):
            expire_at = utcnow() + timedelta(seconds=error.retry_after)
            await interaction.response.send_message(
                f"Время ожидания: {discord.utils.format_dt(expire_at, 'R')}",
                ephemeral=True,
                silent=True,
            )
            return
        if isinstance(error, app_commands.CheckFailure):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title="Доступ запрещён",
                description="Проверка доступа к команде не пройдена.",
                ephemeral=True,
            )
            return
        if isinstance(error, StupidBotError):
            # Handle our custom domain exceptions
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка",
                description=error.user_message,
                ephemeral=True,
            )
            return

        # Unpack CommandInvokeError if it wraps a StupidBotError
        original = getattr(error, "original", None)
        if isinstance(original, StupidBotError):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Ошибка",
                description=original.user_message,
                ephemeral=True,
            )
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.ERROR,
            title="Внутренняя ошибка",
            description="Не удалось выполнить команду. Детали записаны в лог.",
            delete_after=300,
            ephemeral=True,
            error_info=type(error).__name__,
        )
        logger.error("Unhandled app command error: %s", error, exc_info=error)
