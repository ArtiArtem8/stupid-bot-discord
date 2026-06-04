"""Feedback UI Module.

This module provides a unified interface for sending standardized feedback messages
to users via Discord interactions. It supports various feedback types (Success,
Info, Warning, Error), custom embeds, and automatic report button generation for errors.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Self, cast, overload

import discord
from discord.ui import Button, View
from discord.utils import (
    MISSING,  # pyright: ignore[reportAny]
    _MissingSentinel,  # pyright: ignore[reportPrivateUsage]
    format_dt,
    utcnow,
)

import config
from utils import SafeEmbed

type ReportCallback = Callable[[discord.Interaction, str | None], Awaitable[None]]


@dataclass(slots=True)
class FeedbackPayload:
    """Resolved data that preserves MISSING to avoid clearing existing views."""

    embed: discord.Embed
    view: View | _MissingSentinel
    delete_after: float | None
    ephemeral: bool


class FeedbackType(Enum):
    SUCCESS = config.Color.SUCCESS
    INFO = config.Color.INFO
    WARNING = config.Color.WARNING
    ERROR = config.Color.ERROR


class ReportButtonView(View):
    def __init__(
        self,
        user_id: int,
        on_report: ReportCallback,
        error_info: str | None = None,
        timeout: float | None = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_report = on_report
        self.error_info = error_info

    @discord.ui.button(label="Сообщить о проблеме", style=discord.ButtonStyle.danger)
    async def report(self, interaction: discord.Interaction, _: Button[Self]) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Это не ваше сообщение.", ephemeral=True
            )
            return
        await self.on_report(interaction, self.error_info)


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
        feedback_type: FeedbackType,
    ) -> SafeEmbed:
        """Create a standard embed for feedback."""
        embed = SafeEmbed(
            title=title,
            description=description,
            color=feedback_type.value,
        )
        if feedback_type is FeedbackType.ERROR:
            embed.set_thumbnail(url=config.ERROR_THUMBNAIL)
        return embed

    @overload
    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        feedback_type: FeedbackType = FeedbackType.INFO,
        description: str | None = None,
        title: str | None = None,
        delete_after: float | None = None,
        ephemeral: bool = False,
        view: View = MISSING,
        disable_report_btn: bool = False,
        error_info: str | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        feedback_type: FeedbackType = FeedbackType.INFO,
        delete_after: float | None = None,
        ephemeral: bool = False,
        view: View = MISSING,
        disable_report_btn: bool = False,
        error_info: str | None = None,
    ) -> None: ...

    @staticmethod
    async def send(
        interaction: discord.Interaction,
        *,
        feedback_type: FeedbackType = FeedbackType.INFO,
        description: str | None = None,
        title: str | None = None,
        delete_after: float | None = None,
        ephemeral: bool = False,
        view: View = MISSING,
        disable_report_btn: bool = False,
        embed: discord.Embed = MISSING,
        error_info: str | None = None,
    ) -> None:
        """Send a standardized feedback message.

        Args:
            interaction: The interaction to respond to.
            feedback_type: The type of feedback (SUCCESS, INFO, WARNING, ERROR)
            description: The main content of the message.
            title: Optional title.
            delete_after: Auto-delete after N seconds.
            ephemeral: Whether the message is ephemeral.
            view: Optional custom view.
            disable_report_btn: If True, suppresses the Report button for ERROR type.
            embed: Optional custom embed. If provided, type/description/title are
                ignored.
            error_info: Optional error information to pre-fill the report modal.

        """
        payload = FeedbackUI._build_payload(
            interaction,
            feedback_type=feedback_type,
            description=description,
            title=title,
            delete_after=delete_after,
            ephemeral=ephemeral,
            view=view,
            disable_report_btn=disable_report_btn,
            embed=embed,
            error_info=error_info,
        )
        await FeedbackUI._send_payload(interaction, payload)

    @staticmethod
    def _resolve_embed(
        embed: discord.Embed,
        title: str | None,
        description: str | None,
        feedback_type: FeedbackType,
    ) -> discord.Embed:
        if embed is MISSING:
            return FeedbackUI.make_embed(title, description or "", feedback_type)
        return embed

    @staticmethod
    def _resolve_view(
        interaction: discord.Interaction,
        feedback_type: FeedbackType,
        view: View | _MissingSentinel,
        disable_report_btn: bool,
        error_info: str | None,
    ) -> View | _MissingSentinel:
        if (
            feedback_type is FeedbackType.ERROR
            and not disable_report_btn
            and view is MISSING
        ):
            if FeedbackUI._default_report_callback is None:
                raise RuntimeError(
                    "FeedbackUI not configured. Call FeedbackUI.configure() at startup."
                )
            return ReportButtonView(
                interaction.user.id,
                FeedbackUI._default_report_callback,
                error_info=error_info,
            )
        return view

    @staticmethod
    def _add_delete_timer(embed: discord.Embed, delete_after: float | None) -> None:
        if delete_after:
            expire_at = utcnow() + timedelta(seconds=delete_after)
            timer = f"-# Удалится {format_dt(expire_at, style='R')}"
            if isinstance(embed, SafeEmbed):
                embed.safe_add_field(name="", value=timer, inline=False)
            else:
                embed.add_field(name="", value=timer, inline=False)

    @staticmethod
    def _build_payload(
        interaction: discord.Interaction,
        *,
        feedback_type: FeedbackType,
        description: str | None,
        title: str | None,
        delete_after: float | None,
        ephemeral: bool,
        view: View | _MissingSentinel,
        disable_report_btn: bool,
        embed: discord.Embed,
        error_info: str | None,
    ) -> FeedbackPayload:
        resolved_embed = FeedbackUI._resolve_embed(
            embed, title, description, feedback_type
        )
        resolved_view = FeedbackUI._resolve_view(
            interaction, feedback_type, view, disable_report_btn, error_info
        )
        FeedbackUI._add_delete_timer(resolved_embed, delete_after)
        return FeedbackPayload(resolved_embed, resolved_view, delete_after, ephemeral)

    @staticmethod
    async def _send_payload(
        interaction: discord.Interaction, payload: FeedbackPayload
    ) -> None:
        if interaction.response.is_done():
            await FeedbackUI._send_after_response_done(interaction, payload)
            return
        await FeedbackUI._send_initial_response(interaction, payload)

    @staticmethod
    async def _send_after_response_done(
        interaction: discord.Interaction, payload: FeedbackPayload
    ) -> None:
        # discord.py accepts MISSING at runtime, although its public overloads omit it.
        view = cast(View, payload.view)
        if (
            interaction.response.type
            is discord.InteractionResponseType.deferred_channel_message
        ):
            message = await interaction.edit_original_response(
                embed=payload.embed, view=view
            )
        else:
            message = await interaction.followup.send(
                embed=payload.embed,
                view=view,
                ephemeral=payload.ephemeral,
                silent=True,
                wait=True,
            )
        if payload.delete_after:
            await message.delete(delay=payload.delete_after)

    @staticmethod
    async def _send_initial_response(
        interaction: discord.Interaction, payload: FeedbackPayload
    ) -> None:
        # discord.py accepts MISSING at runtime, although its public overloads omit it.
        view = cast(View, payload.view)
        await interaction.response.send_message(
            embed=payload.embed,
            view=view,
            ephemeral=payload.ephemeral,
            delete_after=payload.delete_after,
            silent=True,
        )
