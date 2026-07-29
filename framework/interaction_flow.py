"""Interaction acknowledgement helpers."""

import asyncio
import logging
from collections.abc import Coroutine

import discord
from discord import Interaction

logger = logging.getLogger(__name__)


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
    """Acknowledge a component without displaying a loading state."""
    await _try_defer(
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
    if interaction.response.is_done():
        return

    try:
        await interaction.response.defer(
            thinking=thinking,
            ephemeral=ephemeral,
        )
    except discord.InteractionResponded:
        return
    except discord.HTTPException:
        logger.debug(
            "Interaction defer was not accepted.",
            exc_info=True,
        )
