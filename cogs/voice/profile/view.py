"""One owner-only refresh control for a voice profile attachment."""

import asyncio
from collections.abc import Awaitable, Callable

import discord


class VoiceProfileView(discord.ui.View):
    """Refresh the original message once per render for about ten minutes."""

    def __init__(
        self, owner_id: int, refresh: Callable[[discord.Interaction], Awaitable[None]]
    ) -> None:
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self._on_refresh = refresh
        self._render_lock = asyncio.Lock()

    @discord.ui.button(
        label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["VoiceProfileView"],
    ) -> None:
        """Keep concurrent clicks from launching duplicate history/render work."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Это не ваша карточка.", ephemeral=True
            )
            return
        if self._render_lock.locked():
            await interaction.response.send_message(
                "Карточка уже обновляется.", ephemeral=True
            )
            return
        async with self._render_lock:
            await interaction.response.defer()
            await self._on_refresh(interaction)
