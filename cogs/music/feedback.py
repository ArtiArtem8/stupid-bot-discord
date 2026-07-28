"""Discord feedback helpers for the music cog."""

import logging

from discord import Interaction

from framework import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)


async def send_error(interaction: Interaction, message: str) -> None:
    """Send an error feedback."""
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.ERROR,
        description=message,
        delete_after=600,
    )


async def send_warning(
    interaction: Interaction,
    message: str,
    title: str | None = None,
    ephemeral: bool = True,
    delete_after: float | None = None,
) -> None:
    """Send a warning feedback."""
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.WARNING,
        description=message,
        ephemeral=ephemeral,
        title=title,
        delete_after=delete_after,
    )


async def send_warning_no_player(interaction: Interaction) -> None:
    """Send a warning feedback about there is no player."""
    await send_warning(interaction, "Нет проигрывателя")


async def send_info(
    interaction: Interaction,
    message: str,
    delete_after: float | None = 60,
    title: str | None = None,
) -> None:
    """Send info feedback."""
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.INFO,
        description=message,
        title=title,
        delete_after=delete_after,
    )


async def send_success(
    interaction: Interaction, message: str, delete_after: float | None = 60
) -> None:
    """Send success feedback."""
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.SUCCESS,
        description=message,
        delete_after=delete_after,
    )
