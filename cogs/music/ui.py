"""UI helpers for Music Cog."""

import logging
from datetime import timedelta

from discord import Interaction

from framework import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)

MAX_TIMEDELTA_DAYS = 999_999_999


def format_duration(ms: int | float) -> str:
    """Helper to convert milliseconds to timedelta stripping microseconds.

    Note:
        Lavalink returns 2**63 - 1 ms for live streams.

    """
    try:
        total = timedelta(seconds=ms / 1_000.0)
    except OverflowError:
        total = timedelta(days=min(MAX_TIMEDELTA_DAYS, ms // 86_400_000))
    except ValueError:
        return "NaN"
    total -= timedelta(microseconds=total.microseconds)
    if total.days >= MAX_TIMEDELTA_DAYS - 1_000_000:
        return "∞"
    if total.days >= 14:
        return str(total.days) + " days"
    return str(total)


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
) -> None:
    """Send a warning feedback."""
    await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.WARNING,
        description=message,
        ephemeral=ephemeral,
        title=title,
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
