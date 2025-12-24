"""Decorators for Discord commands."""

import functools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Concatenate, cast

import discord
from discord import Interaction

from framework.feedback_ui import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)

type AsyncFunc[T, **P] = Callable[P, Awaitable[T]]
type CommandCallback[CogT, T, **P] = Callable[
    Concatenate[CogT, Interaction, P],
    Coroutine[Any, Any, T],
]


def handle_errors[CogT, T, **P]() -> Callable[
    [CommandCallback[CogT, T, P]], CommandCallback[CogT, T | None, P]
]:
    """Decorator to add error handling to asynchronous functions.

    This decorator wraps the provided function to catch and handle
    exceptions that may occur during its execution, specifically
    Discord-related exceptions and any other unexpected errors.
    Appropriate error messages are sent as responses to the Discord
    interaction, ensuring a graceful failure with user feedback.

    Returns:
        A decorated function with error handling logic.

    """

    def decorator(
        func: CommandCallback[CogT, T, P],
    ) -> CommandCallback[CogT, T | None, P]:
        @functools.wraps(func)
        async def wrapper(
            self: CogT,
            interaction: Interaction,
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> T | None:
            """Wrapper that adds error handling."""
            try:
                return await func(self, interaction, *args, **kwargs)
            except discord.DiscordException as e:
                logger.exception(f"Discord error in {func.__name__}")
                await FeedbackUI.send(
                    interaction,
                    title="Discord Ошибка",
                    feedback_type=FeedbackType.ERROR,
                    description=f"❌ {type(e).__name__}: {e}",
                    delete_after=600,
                    error_info=str(e),
                )
            except Exception as e:
                logger.exception(f"Unexpected error in {func.__name__}")
                await FeedbackUI.send(
                    interaction,
                    feedback_type=FeedbackType.ERROR,
                    title="Внутренняя ошибка",
                    description=f"❌ {type(e).__name__}: {e}",
                    delete_after=600,
                    error_info=str(e),
                )
            return None

        return cast(CommandCallback[CogT, T | None, P], wrapper)

    return decorator
