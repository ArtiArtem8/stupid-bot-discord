"""Owner-only controls and timeout cleanup for a voice profile attachment."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Self, override

import discord
from discord import app_commands

from framework.error_handler import handle_app_command_error
from resources import RESTART_EMOJI, TRASH_EMOJI

logger = logging.getLogger(__name__)


class VoiceProfileView(discord.ui.View):
    """Keep one card's actions serialized; remove controls after inactivity."""

    def __init__(
        self,
        owner_id: int,
        refresh: Callable[[discord.Interaction], Awaitable[None]],
        *,
        private: bool = True,
    ) -> None:
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.message: discord.InteractionMessage | None = None
        self._on_refresh = refresh
        # A render must finish before deletion or timeout removes its controls.
        self._action_lock = asyncio.Lock()
        if private:
            self.remove_item(self.delete_button)

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Это не ваша карточка.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(emoji=RESTART_EMOJI, style=discord.ButtonStyle.secondary)
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["VoiceProfileView"],
    ) -> None:
        """Keep concurrent clicks from launching duplicate history/render work."""
        if self._action_lock.locked():
            await interaction.response.send_message(
                "Карточка уже обновляется.", ephemeral=True
            )
            return
        async with self._action_lock:
            await interaction.response.defer()
            if not self.is_finished():
                await self._on_refresh(interaction)

    @discord.ui.button(emoji=TRASH_EMOJI, style=discord.ButtonStyle.danger)
    async def delete_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["VoiceProfileView"],
    ) -> None:
        await interaction.response.defer()
        async with self._action_lock:
            if self.is_finished():
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
        item: discord.ui.Item[Self],
        /,
    ) -> None:
        del item
        wrapped = app_commands.AppCommandError("Voice profile interaction failed")
        wrapped.__cause__ = error
        await handle_app_command_error(interaction, wrapped)
