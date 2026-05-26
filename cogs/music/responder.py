"""Interaction acknowledgement policy for music commands and controls."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

import discord
from discord import Interaction

from framework import FeedbackType, FeedbackUI

logger = logging.getLogger(__name__)


class MusicInteractionResponder:
    """Keep music interactions responsive without eager thinking messages."""

    def __init__(self, interaction: Interaction, *, budget: float = 1.5) -> None:
        self.interaction = interaction
        self.budget = budget

    @property
    def responded(self) -> bool:
        """Whether the initial Discord interaction response is already used."""
        return self.interaction.response.is_done()

    async def send_private_failure(
        self,
        message: str,
        *,
        feedback_type: FeedbackType = FeedbackType.WARNING,
    ) -> None:
        """Send a fast ephemeral preflight failure without displaying thinking UI."""
        try:
            await FeedbackUI.send(
                self.interaction,
                feedback_type=feedback_type,
                description=message,
                ephemeral=True,
                disable_report_btn=True,
            )
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Interaction expired while sending music preflight failure.")

    async def defer(self, *, ephemeral: bool = False) -> None:
        """Acknowledge once, ignoring an interaction that has already expired."""
        if self.responded:
            return
        try:
            await self.interaction.response.defer(thinking=True, ephemeral=ephemeral)
        except discord.InteractionResponded:
            return
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Interaction expired before music defer was accepted.")

    async def acknowledge_component(self) -> None:
        """Acknowledge a component before potentially slow player operations."""
        if self.responded:
            return
        try:
            await self.interaction.response.defer()
        except discord.InteractionResponded:
            return
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Component interaction expired before acknowledgement.")

    async def await_with_defer_budget[T](
        self, operation: Awaitable[T], *, ephemeral: bool = False
    ) -> T:
        """Run work and defer only after budget; visibility is selected at defer."""
        task = asyncio.ensure_future(operation)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self.budget)
        except asyncio.TimeoutError:
            await self.defer(ephemeral=ephemeral)
            return await task
