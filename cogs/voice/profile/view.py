"""Owner-only progressive disclosure with atomic attachment updates."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Self, override

import discord
from discord import app_commands

from cogs.voice.profile.attachments import detail_embed, retained_attachments
from cogs.voice.profile.detail_models import ProfileLook
from cogs.voice.profile.media import RenderBusyError
from framework.authorization import check_component_access
from framework.error_handler import handle_app_command_error
from framework.feedback_ui import FeedbackType, FeedbackUI
from resources import (
    RESTART_EMOJI,
    SOCIAL_EMOJI,
    STATISTIC_EMOJI,
    TRASH_EMOJI,
    XP_EMOJI,
)

logger = logging.getLogger(__name__)


class ProfileDetail(StrEnum):
    """The three independently revealable cards, in user-selected order."""

    ACTIVITY = "activity"
    PEOPLE = "people"
    XP = "xp"


class ProfileAction(StrEnum):
    """Concrete controls for details and message actions."""

    ACTIVITY = "activity"
    PEOPLE = "people"
    XP = "xp"
    REFRESH = "refresh"
    DELETE = "delete"


_ACTION_EMOJIS = {
    ProfileAction.ACTIVITY: STATISTIC_EMOJI,
    ProfileAction.PEOPLE: SOCIAL_EMOJI,
    ProfileAction.XP: XP_EMOJI,
    ProfileAction.REFRESH: RESTART_EMOJI,
    ProfileAction.DELETE: TRASH_EMOJI,
}


class ProfileActionButton(discord.ui.Button["VoiceProfileView"]):
    """Forward a concrete action to the owning View."""

    def __init__(self, action: ProfileAction) -> None:
        super().__init__(
            emoji=_ACTION_EMOJIS[action],
            style=(
                discord.ButtonStyle.danger
                if action is ProfileAction.DELETE
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"voice-profile:{action.value}",
        )
        self.action = action

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.perform(self.action, interaction)
        else:
            # An already dispatched click can outlive successful button removal.
            await interaction.response.defer()


class VoiceProfileView(discord.ui.View):
    """Serialize reveals, refresh and deletion; retain only displayed identity.

    A second click can arrive while rendering awaits. The action lock protects
    the message and revealed_order together, and duplicates are checked inside
    it. A provisional serialization omits the reveal button during upload; the
    actual control and revealed state change only after Discord accepts the edit.
    """

    def __init__(
        self,
        owner_id: int,
        refresh: Callable[[discord.Interaction], Awaitable[None]],
        *,
        reveal: Callable[[discord.Interaction, ProfileDetail], Awaitable[discord.File]],
        private: bool = True,
    ) -> None:
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.private = private
        self.revealed_order: list[ProfileDetail] = []
        self.look: ProfileLook | None = None
        self.message: discord.InteractionMessage | None = None
        self._on_refresh = refresh
        self._on_reveal = reveal
        self._action_lock = asyncio.Lock()
        self._pending_action: ProfileAction | None = None
        self.buttons: dict[ProfileAction, ProfileActionButton] = {}
        for action in ProfileAction:
            if private and action is ProfileAction.DELETE:
                continue
            button = ProfileActionButton(action)
            self.buttons[action] = button
            self.add_item(button)

    @override
    def to_components(self) -> list[dict[str, object]]:
        # Serialize a successful edit's proposed controls without mutating the
        # live View before the network await. All five controls fit one row.
        components = [
            button.to_component_dict()
            for button in self.buttons.values()
            if button in self.children and button.action != self._pending_action
        ]
        return [{"type": 1, "components": components}] if components else []

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Это не ваша карточка.", ephemeral=True
            )
            return False
        return True

    async def perform(
        self, action: ProfileAction, interaction: discord.Interaction
    ) -> None:
        """Authorize and acknowledge before waiting for a running card action."""
        if not await self.interaction_check(interaction):
            return
        # Owners retain the existing ability to remove their published message.
        if action is not ProfileAction.DELETE and not await check_component_access(
            interaction
        ):
            return
        if action is ProfileAction.REFRESH and self._action_lock.locked():
            await interaction.response.send_message(
                "Карточка уже обновляется.", ephemeral=True
            )
            return
        await interaction.response.defer()
        async with self._action_lock:
            if self.is_finished():
                return
            await self._perform_locked(action, interaction)

    async def _perform_locked(
        self, action: ProfileAction, interaction: discord.Interaction
    ) -> None:
        try:
            if action is ProfileAction.DELETE:
                await self._delete(interaction)
            elif action is ProfileAction.REFRESH:
                await self._on_refresh(interaction)
            else:
                detail = ProfileDetail(action.value)
                if detail not in self.revealed_order:
                    await self._reveal(interaction, detail)
        except RenderBusyError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Карточки сейчас заняты. Попробуйте чуть позже.",
                ephemeral=True,
            )
        except Exception:
            # This UI boundary owns retry feedback; no names or statistics in logs.
            logger.warning("Voice profile %s failed", action.value)
            logger.debug("Voice profile action failure", exc_info=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Не удалось обновить карточку. Попробуйте ещё раз.",
                ephemeral=True,
            )

    async def _reveal(
        self, interaction: discord.Interaction, detail: ProfileDetail
    ) -> None:
        if self.message is None:
            raise RuntimeError("Profile message is unavailable")
        attachment = await self._on_reveal(interaction, detail)
        try:
            self._pending_action = ProfileAction(detail.value)
            # Use our latest successful response, never the click's stale message.
            message = await interaction.edit_original_response(
                attachments=[*retained_attachments(self.message), attachment],
                embeds=[
                    detail_embed(f"voice-{kind.value}.png")
                    for kind in (*self.revealed_order, detail)
                ],
                view=self,
            )
        finally:
            self._pending_action = None
            attachment.close()
            attachment.fp.close()
        self.message = message
        self.remove_item(self.buttons[ProfileAction(detail.value)])
        self.revealed_order.append(detail)

    async def _delete(self, interaction: discord.Interaction) -> None:
        if self.private:
            return
        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass
        self.stop()

    @override
    async def on_timeout(self) -> None:
        self.stop()
        async with self._action_lock:
            if self.message is None:
                return
            try:
                await self.message.edit(view=None)
            except discord.NotFound:
                pass
            except discord.HTTPException as error:
                logger.warning(
                    "Voice profile controls cleanup failed: HTTP %s", error.status
                )
                logger.debug("Profile controls cleanup traceback", exc_info=True)

    @override
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Self],
        /,
    ) -> None:
        wrapped = app_commands.AppCommandError("Voice profile interaction failed")
        wrapped.__cause__ = error
        await handle_app_command_error(interaction, wrapped)
