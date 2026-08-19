"""Global application-command error routing."""

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.utils import utcnow

from framework.exceptions import BlockedUserError
from framework.feedback_ui import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)


async def handle_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    """Send one safe response for an application-command failure."""
    if isinstance(error, BlockedUserError):
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="⛔ Доступ к командам запрещён.",
            ephemeral=True,
        )
        return
    if isinstance(error, app_commands.NoPrivateMessage):
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="Команда может быть использована только на сервере",
            ephemeral=True,
        )
        return
    if isinstance(error, app_commands.CommandOnCooldown):
        expire_at = utcnow() + timedelta(seconds=error.retry_after)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description=(f"Время ожидания: {discord.utils.format_dt(expire_at, 'R')}"),
            ephemeral=True,
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

    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
        if isinstance(original, discord.DiscordException):
            logger.error(
                "Discord exception in app command %r",
                interaction.command,
                exc_info=original,
            )
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                title="Discord Ошибка",
                description=(
                    "Не удалось выполнить действие в Discord. Попробуйте ещё раз."
                ),
                delete_after=600,
                ephemeral=True,
                error_info=type(original).__name__,
            )
            return
    else:
        original = error

    logger.error(
        "Unhandled exception in app command %r",
        interaction.command,
        exc_info=original,
    )
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.ERROR,
        title="Внутренняя ошибка",
        description="Не удалось выполнить команду. Детали записаны в лог.",
        delete_after=300,
        ephemeral=True,
        error_info=type(original).__name__,
    )
