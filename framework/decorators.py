"""Decorators for Discord commands."""

import functools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Concatenate, cast

import discord
from discord import Interaction

from framework.feedback_ui import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)

type AsyncFunc[T, **P] = Callable[P, Awaitable[T]]
type CommandCallback[CogT, T, **P] = Callable[
    Concatenate[CogT, Interaction, P],
    Coroutine[object, object, T],
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
                logger.exception("Discord error in %s", func.__name__)
                await FeedbackUI.send(
                    interaction,
                    title="Discord Ошибка",
                    feedback_type=FeedbackType.ERROR,
                    description=(
                        "Не удалось выполнить действие в Discord. Попробуйте ещё раз."
                    ),
                    delete_after=600,
                    error_info=str(e),
                )
            except Exception as e:
                logger.exception("Unexpected error in %s", func.__name__)
                await FeedbackUI.send(
                    interaction,
                    feedback_type=FeedbackType.ERROR,
                    title="Внутренняя ошибка",
                    description="Внутренняя ошибка. Детали записаны в лог.",
                    delete_after=600,
                    error_info=str(e),
                )
            return None

        return cast(CommandCallback[CogT, T | None, P], wrapper)

    return decorator
