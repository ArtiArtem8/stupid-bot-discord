"""Queue pagination views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Self, override

import discord
from discord import Interaction
from discord.utils import escape_markdown

import config
from api.music import QueueSnapshot, RepeatMode
from framework import PRIMARY, BasePaginator, CallbackButton, PaginationData
from utils import TextPaginator

from ..ui import send_warning

type QueueRefreshCallback = Callable[[], Awaitable[QueueSnapshot | None]]


class QueuePaginationAdapter(PaginationData):
    """Adapts music queue data for the paginator."""

    def __init__(self, snapshot: QueueSnapshot, page_size: int = 20) -> None:
        self.snapshot = snapshot
        self.page_size = page_size
        self._paginator = self._build_paginator(snapshot)

    def update_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.snapshot = snapshot
        self._paginator = self._build_paginator(snapshot)

    @override
    async def get_page_count(self) -> int:
        return max(1, len(self._paginator.pages))

    def _build_paginator(self, snapshot: QueueSnapshot) -> TextPaginator:
        return TextPaginator(
            [
                f"{i}. [{entry.track.title}]({entry.track.uri})"
                for i, entry in enumerate(snapshot.queue, 1)
            ],
            page_size=self.page_size,
            max_length=config.MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

    @override
    def make_embed(self, page: int) -> discord.Embed:
        embed = discord.Embed(title="Очередь воспроизведения", color=config.Color.INFO)
        current = self.snapshot.current

        if current:
            track = current.track
            embed.add_field(
                name="Сейчас играет",
                value=f"[{escape_markdown(track.title)}]({track.uri})",
                inline=False,
            )
            if track.artwork_url:
                embed.set_thumbnail(url=track.artwork_url)
        else:
            embed.description = "Ничего не играет."

        if 0 <= page < len(self._paginator.pages):
            embed.add_field(
                name="Далее",
                value=self._paginator.pages[page],
                inline=False,
            )

        repeat_str = (
            "выкл."
            if self.snapshot.repeat_mode is RepeatMode.OFF
            else self.snapshot.repeat_mode.value
        )
        total_pages = max(1, len(self._paginator.pages))
        embed.set_footer(
            text=(
                f"Стр. {page + 1}/{total_pages} • "
                f"В очереди: {self._paginator.total_items} • "
                f"Повтор: {repeat_str}"
            )
        )
        return embed

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        await send_warning(
            interaction, "Попрошу не трогать, это не ваше сообщение.", ephemeral=True
        )


class QueuePaginator(BasePaginator):
    """Specialized paginator with a Refresh button."""

    def __init__(
        self,
        adapter: QueuePaginationAdapter,
        refresh_callback: QueueRefreshCallback,
        user_id: int,
    ) -> None:
        super().__init__(adapter, user_id, show_first_last=True)
        self.adapter = adapter
        self.refresh_callback = refresh_callback

        self.refresh_btn = CallbackButton[Self](
            self.refresh, label="⭮", style=PRIMARY, row=1
        )
        self.add_item(self.refresh_btn)

    async def refresh(self, interaction: Interaction) -> None:
        new_data = await self.refresh_callback()
        if new_data:
            self.adapter.update_snapshot(new_data)
            self.page = 0
            await self._update_view(interaction)
        else:
            await send_warning(
                interaction, "Не удалось обновить очередь", ephemeral=True
            )
            self.stop()
