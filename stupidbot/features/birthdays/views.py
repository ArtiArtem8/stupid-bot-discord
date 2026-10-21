"""Versioned confirmation before removing a saved birthday."""

import logging
from typing import Self, override

import discord
from discord import Interaction
from discord.ui import Button

from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import ack_component, defer_command
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import PresentationContext

logger = logging.getLogger(__name__)


class ConfirmDeleteView(discord.ui.View):
    """One confirmation claims one versioned deletion and then closes."""

    def __init__(
        self,
        user_id: int,
        guild_id: int,
        manager: BirthdayRepository,
        expected_version: int,
        *,
        context: PresentationContext,
    ) -> None:
        super().__init__(timeout=30)
        self.context = context
        self.messages = catalog("birthdays", context.language)
        self.confirm.label = self.messages.pgettext("ui", "Yes")
        self.cancel.label = self.messages.pgettext("ui", "No")
        self.user_id = user_id
        self.expected_version = expected_version
        self.guild_id = guild_id
        self.manager = manager
        self.message: discord.InteractionMessage | None = None
        self._closed = False

    async def _prepare_action(
        self, interaction: Interaction, *, require_access: bool
    ) -> bool:
        if interaction.user.id != self.user_id:
            await defer_command(interaction, ephemeral=True)
            context = await interaction_context(interaction, private=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=catalog("birthdays", context.language).pgettext(
                    "ui", "You cannot perform this action"
                ),
                ephemeral=True,
            )
            return False
        await ack_component(interaction)
        if require_access and not await check_component_access(interaction):
            return False
        if self.is_finished():
            return False
        # Other callbacks may already be waiting for their access check.
        self._finish()
        return True

    def _finish(self) -> None:
        self._closed = True
        self.clear_items()
        self.stop()

    async def _show_result(
        self, interaction: Interaction, text: str, feedback_type: FeedbackType
    ) -> None:
        await interaction.edit_original_response(
            embed=FeedbackUI.make_embed(None, text, feedback_type), view=None
        )

    @discord.ui.button(
        label=pgettext_noop("ui", "Yes"), style=discord.ButtonStyle.green
    )
    async def confirm(self, interaction: Interaction, _: Button[Self]) -> None:
        if not await self._prepare_action(interaction, require_access=True):
            return
        try:
            guild_exists, cleared = await self.manager.clear_user_birthday(
                self.guild_id, self.user_id, expected_version=self.expected_version
            )
        except Exception:
            logger.exception(
                "Failed to save birthday removal for user %s", self.user_id
            )
            await self._show_result(
                interaction,
                self.messages.pgettext(
                    "remove_failed",
                    "Could not remove the birthday. Open /remove-birthday "
                    "to try again.",
                ),
                FeedbackType.ERROR,
            )
            return
        if not guild_exists:
            await self._show_result(
                interaction,
                self.messages.pgettext("ui", "Server configuration not found"),
                FeedbackType.ERROR,
            )
            return
        if not cleared:
            await self._show_result(
                interaction,
                self.messages.pgettext(
                    "ui",
                    "The date has already changed or been removed. Open a new "
                    "confirmation.",
                ),
                FeedbackType.WARNING,
            )
            return
        await self._show_result(
            interaction,
            self.messages.pgettext("ui", "Your birthday has been removed"),
            FeedbackType.SUCCESS,
        )

    @discord.ui.button(label=pgettext_noop("ui", "No"), style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, _: Button[Self]) -> None:
        if not await self._prepare_action(interaction, require_access=False):
            return
        await self._show_result(
            interaction,
            self.messages.pgettext("ui", "Cancelled"),
            FeedbackType.INFO,
        )

    @override
    async def on_timeout(self) -> None:
        if self._closed:
            return
        self._finish()
        if self.message is None:
            return
        text = self.messages.pgettext(
            "remove_expired",
            "Confirmation expired. Open /remove-birthday to try again.",
        )
        try:
            await self.message.edit(
                embed=FeedbackUI.make_embed(None, text, FeedbackType.INFO), view=None
            )
        except discord.HTTPException:
            logger.debug("Birthday confirmation timeout cleanup failed", exc_info=True)

    @override
    async def on_error(
        self, interaction: Interaction, error: Exception, item: discord.ui.Item[Self], /
    ) -> None:
        await handle_app_command_error(interaction, error)
