# framework/pagination.py
from typing import Any, Protocol, Self, override

import discord
from discord import Interaction
from discord.ui import Button

SECONDARY = discord.ButtonStyle.secondary
""":py:attr:`discord.ButtonStyle.secondary` """
PRIMARY = discord.ButtonStyle.primary
""":py:attr:`discord.ButtonStyle.primary` """
DANGER = discord.ButtonStyle.danger
""":py:attr:`discord.ButtonStyle.danger` """


class PaginationData(Protocol):
    """Protocol defining what data a paginator needs."""

    async def get_page_count(self) -> int:
        """Return the total number of pages."""
        ...

    def make_embed(self, page: int) -> discord.Embed:
        """Create an embed for the specified page."""
        ...

    async def on_unauthorized(self, interaction: Interaction) -> None:
        """Send an unauthorized message."""
        ...


class ManagedView(discord.ui.View):
    """Base view with message tracking and auto-cleanup on timeout."""

    def __init__(self, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self._message: discord.InteractionMessage | None = None

    @override
    async def on_timeout(self) -> None:
        """Disable all buttons and remove view on timeout."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        if self._message:
            try:
                await self._message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()

    async def send(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = False,
        silent: bool = False,
        **kwargs: Any,
    ) -> None:
        """Send the view and track the message."""
        response = await interaction.response.send_message(
            view=self, ephemeral=ephemeral, silent=silent, **kwargs
        )
        if isinstance(response.resource, discord.InteractionMessage):
            self._message = response.resource


class BasePaginator(ManagedView):
    """Base paginator with common navigation logic."""

    def __init__(
        self,
        data: PaginationData,
        user_id: int,
        *,
        timeout: float = 300.0,
        show_first_last: bool = True,
        show_close: bool = True,
    ) -> None:
        super().__init__(timeout=timeout)
        self.data = data
        self.page = 0
        self._user_id = user_id

        self._setup_buttons(show_first_last, show_close)
        self._update_buttons()

    def _setup_buttons(self, show_first_last: bool, show_close: bool) -> None:
        """Setup navigation buttons based on configuration."""
        if show_first_last:
            self.first_btn = Button[Self](label="⏮", style=SECONDARY, row=0)
            self.first_btn.callback = self.first_page
            self.add_item(self.first_btn)

        self.next_btn = Button[Self](label="▶", style=SECONDARY, row=0)
        self.prev_btn = Button[Self](label="◀", style=SECONDARY, row=0)
        self.next_btn.callback = self.next_page
        self.prev_btn.callback = self.prev_page

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

        if show_first_last:
            self.last_btn = Button[Self](label="⏭", style=SECONDARY, row=0)
            self.last_btn.callback = self.last_page
            self.add_item(self.last_btn)

        if show_close:
            self.close_btn = Button[Self](label="✕", style=DANGER, row=0)
            self.close_btn.callback = self.close
            self.add_item(self.close_btn)

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        """Ensure only the command user can interact."""
        if interaction.user.id != self._user_id:
            await self.data.on_unauthorized(interaction)
            return False
        return True

    async def get_total_pages(self) -> int:
        """Get total number of pages."""
        return await self.data.get_page_count()

    def make_embed(self) -> discord.Embed:
        """Create embed for current page."""
        return self.data.make_embed(self.page)

    def _update_buttons(self, total_pages: int = 1) -> None:
        """Update button states based on current page."""
        is_first_page = self.page == 0
        is_last_page = self.page >= total_pages - 1
        disable_nav = total_pages <= 1

        if hasattr(self, "first_btn"):
            self.first_btn.disabled = is_first_page or disable_nav
        if hasattr(self, "last_btn"):
            self.last_btn.disabled = is_last_page or disable_nav

        self.prev_btn.disabled = is_first_page or disable_nav
        self.next_btn.disabled = is_last_page or disable_nav

    async def _update_view(self, interaction: Interaction) -> None:
        """Update the view with current page."""
        total_pages = await self.get_total_pages()
        if total_pages > 0:
            self.page = min(self.page, total_pages - 1)
        self._update_buttons()
        try:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        except discord.NotFound:
            self.stop()

    async def first_page(self, interaction: Interaction) -> None:
        self.page = 0
        await self._update_view(interaction)

    async def prev_page(self, interaction: Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self._update_view(interaction)

    async def next_page(self, interaction: Interaction) -> None:
        self.page = min(self.page + 1, await self.get_total_pages() - 1)
        await self._update_view(interaction)

    async def last_page(self, interaction: Interaction) -> None:
        self.page = await self.get_total_pages() - 1
        await self._update_view(interaction)

    async def close(self, interaction: Interaction) -> None:
        """Close the paginator."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        await interaction.response.edit_message(view=None)
        self.stop()

    @override
    async def send(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = False,
        silent: bool = False,
        **kwargs: Any,
    ) -> None:
        """Send the paginated message."""
        await super().send(
            interaction,
            embed=self.make_embed(),
            ephemeral=ephemeral,
            silent=silent,
            **kwargs,
        )
