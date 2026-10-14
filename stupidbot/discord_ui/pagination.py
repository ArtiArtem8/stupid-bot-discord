from collections.abc import Awaitable, Callable
from typing import Any, Protocol, Self, override

import discord
from discord import Interaction
from discord.ui import Button

from stupidbot.discord_ui.emojis import (
    CLOSE_EMOJI,
    FIRST_PAGE_EMOJI,
    LAST_PAGE_EMOJI,
    NEXT_PAGE_EMOJI,
    PREVIOUS_PAGE_EMOJI,
)


class PaginationData(Protocol):
    """Supply pages and access feedback to BasePaginator."""

    async def get_page_count(self) -> int:
        """Return the total number of pages."""
        ...

    def make_embed(self, page: int) -> discord.Embed:
        """Create an embed for the specified page."""
        ...

    async def on_unauthorized(self, interaction: Interaction) -> None:
        """Respond when someone other than the page owner uses the controls."""
        ...


type ButtonCallback = Callable[[Interaction], Awaitable[None]]


class CallbackButton[ViewT: discord.ui.View | discord.ui.LayoutView](
    discord.ui.Button[ViewT]
):
    """Button that delegates interaction handling to a typed callback."""

    def __init__(
        self,
        callback: ButtonCallback,
        *,
        label: str | None = None,
        emoji: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int | None = None,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            row=row,
        )
        self._callback = callback

    @override
    async def callback(self, interaction: Interaction) -> None:
        await self._callback(interaction)


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
        **kwargs: Any,  # pyright: ignore[reportExplicitAny, reportAny]
    ) -> None:
        """Send the view and track the message."""
        response = await interaction.response.send_message(
            view=self,
            ephemeral=ephemeral,
            silent=silent,
            **kwargs,  # pyright: ignore[reportAny]
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

    def _setup_buttons(self, show_first_last: bool, show_close: bool) -> None:
        if show_first_last:
            self.first_btn = CallbackButton[Self](
                self.first_page, emoji=FIRST_PAGE_EMOJI, row=0
            )
            self.add_item(self.first_btn)

        self.next_btn = CallbackButton[Self](
            self.next_page, emoji=NEXT_PAGE_EMOJI, row=0
        )
        self.prev_btn = CallbackButton[Self](
            self.prev_page, emoji=PREVIOUS_PAGE_EMOJI, row=0
        )

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

        if show_first_last:
            self.last_btn = CallbackButton[Self](
                self.last_page, emoji=LAST_PAGE_EMOJI, row=0
            )
            self.add_item(self.last_btn)

        if show_close:
            self.close_btn = CallbackButton[Self](self.close, emoji=CLOSE_EMOJI, row=0)
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

    async def prepare(self) -> None:
        """Resolve page count and button state before the view is first sent."""
        total_pages = await self.get_total_pages()
        self._update_buttons(total_pages)

    def _update_buttons(self, total_pages: int) -> None:
        is_first_page = self.page == 0
        is_last_page = self.page >= total_pages - 1
        disable_nav = total_pages <= 1

        if hasattr(self, "first_btn"):
            self.first_btn.disabled = is_first_page or disable_nav
        if hasattr(self, "last_btn"):
            self.last_btn.disabled = is_last_page or disable_nav

        self.prev_btn.disabled = is_first_page or disable_nav
        self.next_btn.disabled = is_last_page or disable_nav

    async def _update_view(
        self, interaction: Interaction, *, total_pages: int | None = None
    ) -> None:
        # Navigation reuses the page count that established its target.
        if total_pages is None:
            total_pages = await self.get_total_pages()
        self.page = max(0, min(self.page, total_pages - 1))
        self._update_buttons(total_pages)
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
        total_pages = await self.get_total_pages()
        self.page += 1
        await self._update_view(interaction, total_pages=total_pages)

    async def last_page(self, interaction: Interaction) -> None:
        total_pages = await self.get_total_pages()
        self.page = total_pages - 1
        await self._update_view(interaction, total_pages=total_pages)

    async def close(self, interaction: Interaction) -> None:
        """Stop listening before asking Discord to remove the controls."""
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        self.stop()
        try:
            await interaction.response.edit_message(view=None)
        except discord.NotFound:
            pass

    @override
    async def send(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = False,
        silent: bool = False,
        **kwargs: Any,  # pyright: ignore[reportExplicitAny, reportAny]
    ) -> None:
        """Send the paginated message."""
        await super().send(
            interaction,
            embed=self.make_embed(),
            ephemeral=ephemeral,
            silent=silent,
            **kwargs,
        )
