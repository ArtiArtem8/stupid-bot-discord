"""Feedback UI Module.

This module provides a unified interface for sending standardized feedback messages
to users via Discord interactions. It supports various feedback types (Success,
Info, Warning, Error), custom embeds, and automatic report button generation for errors.
"""

from datetime import timedelta
from enum import Enum
from typing import Awaitable, Callable, Self, overload

import discord
from discord.ui import Button, View
from discord.utils import MISSING, format_dt, utcnow

import config

type ReportCallback = Callable[[discord.Interaction], Awaitable[None]]


class FeedbackType(Enum):
    SUCCESS = config.Color.SUCCESS
    INFO = config.Color.INFO
    WARNING = config.Color.WARNING
    ERROR = config.Color.ERROR


class ReportButtonView(View):
    def __init__(
        self, user_id: int, on_report: ReportCallback, timeout: float | None = 180
    ) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_report = on_report

    @discord.ui.button(label="Сообщить о проблеме", style=discord.ButtonStyle.danger)
    async def report(
        self, interaction: discord.Interaction, button: Button[Self]
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Это не ваше сообщение.", ephemeral=True
            )
            return
        await self.on_report(interaction)


class FeedbackUI:
    """Unified UI for sending feedback messages (Success, Info, Warning, Error)."""

    _default_report_callback: ReportCallback | None = None

    @classmethod
    def configure(cls, report_callback: ReportCallback) -> None:
        """Configure default report handler. Call once at bot startup."""
        cls._default_report_callback = report_callback

    @staticmethod
    def make_embed(
        title: str | None,
        description: str,
        color: int,
    ) -> discord.Embed:
        """Create a standard embed for feedback."""
        return discord.Embed(
            title=title,
            description=description,
            color=color,
        )

    @overload
    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        type: FeedbackType = FeedbackType.INFO,
        description: str | None = None,
        title: str | None = None,
        delete_after: float | None = None,
        ephemeral: bool = True,
        view: View = MISSING,
        disable_report_btn: bool = False,
    ) -> None: ...

    @overload
    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        type: FeedbackType = FeedbackType.INFO,
        delete_after: float | None = None,
        ephemeral: bool = True,
        view: View = MISSING,
        disable_report_btn: bool = False,
    ) -> None: ...

    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        type: FeedbackType = FeedbackType.INFO,
        description: str | None = None,
        title: str | None = None,
        delete_after: float | None = None,
        ephemeral: bool = True,
        view: View = MISSING,
        disable_report_btn: bool = False,
        embed: discord.Embed = MISSING,
    ) -> None:
        """Send a standardized feedback message.

        Args:
            interaction: The interaction to respond to.
            type: The type of feedback (SUCCESS, INFO, WARNING, ERROR). Defaults to
                INFO.
            description: The main content of the message.
            title: Optional title.
            delete_after: Auto-delete after N seconds.
            ephemeral: Whether the message is ephemeral.
            view: Optional custom view.
            disable_report_btn: If True, suppresses the Report button for ERROR type.
            embed: Optional custom embed. If provided, type/description/title are
                ignored.

        """
        if embed is MISSING:
            if description is None:
                description = ""
            embed = FeedbackUI.make_embed(title, description, type.value)

        if type is FeedbackType.ERROR and not disable_report_btn and view is MISSING:
            if FeedbackUI._default_report_callback is None:
                raise RuntimeError(
                    "FeedbackUI not configured. Call FeedbackUI.configure() at startup."
                )
            view = ReportButtonView(
                interaction.user.id, FeedbackUI._default_report_callback
            )

        if delete_after:
            expire_at = utcnow() + timedelta(seconds=delete_after)
            timer = f"-# Удалится {format_dt(expire_at, style='R')}"
            embed.add_field(name="", value=timer, inline=False)

        if interaction.response.is_done():
            msg = await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=ephemeral,
                silent=True,
                wait=True,
            )
            if delete_after:
                await msg.delete(delay=delete_after)
            return

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=ephemeral,
            delete_after=delete_after,
            silent=True,
        )
