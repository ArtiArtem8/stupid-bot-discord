"""Interaction acknowledgement helpers."""

import asyncio
import logging
from collections.abc import Coroutine

import aiohttp
import discord
from discord import Interaction

from stupidbot.discord_ui.exceptions import InteractionAcknowledgementError

logger = logging.getLogger(__name__)
_DEFERRED_PRIVATE = "stupidbot.response.deferred_private"


async def defer_command(interaction: Interaction, *, ephemeral: bool) -> None:
    """Acknowledge before command work, preserving the initial response audience.

    Failed acknowledgements abort the caller before it can perform side effects.
    A prior response is left intact, including its original visibility.
    """
    await _defer(interaction, thinking=True, ephemeral=ephemeral)


async def deferred_response_is_private(interaction: Interaction) -> bool:
    """Read the acknowledgement audience, including defers made outside this module."""
    private: object = interaction.extras.get(_DEFERRED_PRIVATE)
    if isinstance(private, bool):
        return private
    message = await interaction.original_response()
    private = message.flags.ephemeral
    interaction.extras[_DEFERRED_PRIVATE] = private
    return private


async def run_with_defer[T](
    interaction: Interaction,
    operation: Coroutine[object, object, T],
    *,
    defer_after: float = 1.5,
    ephemeral: bool = False,
) -> T:
    """Run an owned operation and defer only when it exceeds the UX threshold."""
    task = asyncio.create_task(operation)

    try:
        await asyncio.wait(
            (task,),
            timeout=defer_after,
        )

        if not task.done():
            await _try_defer(
                interaction,
                thinking=True,
                ephemeral=ephemeral,
            )

        return await task
    finally:
        if not task.done():
            task.cancel()

        await asyncio.gather(task, return_exceptions=True)


async def ack_component(interaction: Interaction) -> None:
    """Acknowledge before component work without displaying a loading state."""
    await _defer(
        interaction,
        thinking=False,
        ephemeral=False,
    )


async def _try_defer(
    interaction: Interaction,
    *,
    thinking: bool,
    ephemeral: bool,
) -> None:
    try:
        await _defer(interaction, thinking=thinking, ephemeral=ephemeral)
    except InteractionAcknowledgementError:
        # The operation was already admitted before this delayed acknowledgement.
        logger.debug("Interaction defer was not accepted.", exc_info=True)


async def _defer(interaction: Interaction, *, thinking: bool, ephemeral: bool) -> None:
    if interaction.response.is_done():
        return

    try:
        await interaction.response.defer(
            thinking=thinking,
            ephemeral=ephemeral,
        )
    except discord.InteractionResponded:
        return
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as error:
        raise InteractionAcknowledgementError() from error
    if thinking:
        interaction.extras[_DEFERRED_PRIVATE] = ephemeral
