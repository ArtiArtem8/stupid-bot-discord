"""Shared error routing for commands and explicitly delegated UI callbacks."""

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.utils import utcnow

from stupidbot.discord_ui.exceptions import (
    AccessCheckUnavailableError,
    BlockedUserError,
    CommandFeedbackError,
    InteractionAcknowledgementError,
)
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import defer_command
from stupidbot.discord_ui.messages import FeedbackMessages
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import error_context

logger = logging.getLogger(__name__)


async def handle_app_command_error(
    interaction: discord.Interaction,
    error: Exception,
) -> None:
    """Send one private response; unwrap command failures before classification."""
    original = (
        error.original if isinstance(error, app_commands.CommandInvokeError) else error
    )
    if not await _acknowledge_error(interaction, original):
        return
    if isinstance(
        original, (app_commands.CheckFailure, app_commands.CommandOnCooldown)
    ):
        await _send_rejection(interaction, original)
        return
    context = await error_context(interaction)
    messages = catalog("discord_ui", context.language)
    if isinstance(original, discord.DiscordException) and not isinstance(
        original, app_commands.AppCommandError
    ):
        category = "Discord command failed"
        title = messages.pgettext("discord_title", "Discord error")
        description = messages.pgettext(
            "discord_error",
            "Could not complete the action in Discord. Please try again.",
        )
        delete_after = 600
    else:
        category = "Unhandled command failure"
        title = messages.pgettext("internal_title", "Internal error")
        description = messages.pgettext(
            "internal_error", "Could not complete the command. Details were logged."
        )
        delete_after = 300

    logger.error(
        "%s: command=%r guild=%s channel=%s user=%s interaction=%s",
        category,
        interaction.command,
        interaction.guild_id,
        interaction.channel_id,
        interaction.user.id,
        interaction.id,
        exc_info=original,
    )
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.ERROR,
        title=title,
        description=description,
        delete_after=delete_after,
        ephemeral=True,
        error_info=type(original).__name__,
    )


async def _send_rejection(
    interaction: discord.Interaction,
    error: app_commands.CheckFailure | app_commands.CommandOnCooldown,
) -> None:
    """Present expected refusals without treating them as operational failures."""
    if isinstance(error, CommandFeedbackError):
        await FeedbackUI.send(
            interaction,
            description=error.description,
            feedback_type=(
                FeedbackType.INFO if error.severity == "info" else FeedbackType.WARNING
            ),
            ephemeral=True,
            disable_report_btn=True,
        )
        return
    context = await error_context(interaction)
    messages = catalog("discord_ui", context.language)
    if isinstance(error, AccessCheckUnavailableError):
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description=messages.pgettext(
                "access_unavailable", "Could not check access. Please try again."
            ),
            ephemeral=True,
        )
        return
    if isinstance(error, BlockedUserError):
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description=messages.pgettext("blocked", "You cannot use commands."),
            ephemeral=True,
        )
        return
    if isinstance(error, app_commands.NoPrivateMessage):
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description=messages.pgettext(
                "guild_only", "This command can only be used in a server"
            ),
            ephemeral=True,
        )
        return
    if isinstance(error, app_commands.CommandOnCooldown):
        expire_at = utcnow() + timedelta(seconds=error.retry_after)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description=FeedbackMessages(messages).cooldown(
                relative_time=discord.utils.format_dt(expire_at, "R")
            ),
            ephemeral=True,
        )
        return
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.WARNING,
        title=messages.pgettext("access_title", "Access denied"),
        description=messages.pgettext("access", "The command access check failed."),
        ephemeral=True,
    )


async def _acknowledge_error(
    interaction: discord.Interaction, error: Exception
) -> bool:
    if isinstance(error, InteractionAcknowledgementError):
        logger.warning(
            "Interaction acknowledgement failed: interaction=%s error=%s",
            interaction.id,
            type(error.__cause__).__name__,
        )
        return False
    try:
        await defer_command(interaction, ephemeral=True)
    except InteractionAcknowledgementError as failure:
        logger.warning(
            "Error response acknowledgement failed: interaction=%s error=%s",
            interaction.id,
            type(failure.__cause__).__name__,
        )
        return False
    return True
