import logging
from datetime import timedelta

import discord
from discord import Interaction, app_commands
from discord.utils import utcnow

from framework import BlockedUserError, FeedbackType, FeedbackUI, NoGuildError

LOGGER = logging.getLogger("StupidBot")


class CustomErrorCommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        """Event handler for when an app command error occurs."""
        if isinstance(error, BlockedUserError):
            await interaction.response.send_message(
                "⛔ Доступ к командам запрещён.", ephemeral=True
            )
            return
        elif isinstance(error, NoGuildError):
            await interaction.response.send_message(
                "Команда может быть использована только на сервере",
                ephemeral=True,
                silent=True,
            )
            return
        elif isinstance(error, app_commands.CommandOnCooldown):
            expire_at = utcnow() + timedelta(seconds=error.retry_after)
            await interaction.response.send_message(
                f"Время ожидания: {discord.utils.format_dt(expire_at, 'R')}",
                ephemeral=True,
                silent=True,
            )
            return
        elif isinstance(error, app_commands.CheckFailure):
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.ERROR,
            title=str(error),
            delete_after=300,
            ephemeral=False,
            error_info=str(error),
        )
        LOGGER.error(f"Unhandled app command error: {error}", exc_info=error)
