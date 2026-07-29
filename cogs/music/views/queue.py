"""Queue pagination views."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Self, override

import discord
from discord import Interaction

import config
from api.music import MusicResult, QueueEntry, QueueSnapshot, RepeatMode
from framework import (
    DANGER,
    PRIMARY,
    BasePaginator,
    CallbackButton,
    FeedbackType,
    FeedbackUI,
    PaginationData,
)
from utils import TextPaginator

from ..feedback import send_warning
from ..presentation import format_track_link
from ..responder import MusicInteractionResponder

logger = logging.getLogger(__name__)

type QueueRefreshCallback = Callable[[], Awaitable[QueueSnapshot | None]]
type QueuedEntriesRemoveCallback = Callable[
    [int, tuple[QueueEntry, ...], int],
    Awaitable[MusicResult[tuple[QueueEntry, ...]]],
]

STALE_QUEUE_REQUEST_MESSAGE = (
    "Ничего из этого запроса уже не ожидает в очереди. "
    "Возможно, треки уже запустились или очередь была изменена."
)


class QueueUndoView(discord.ui.View):
    """One-shot undo control for the waiting part of one enqueue request."""

    def __init__(
        self,
        *,
        guild_id: int,
        expected_entries: tuple[QueueEntry, ...],
        requester_id: int,
        remove_callback: QueuedEntriesRemoveCallback,
        timeout: float,
    ) -> None:
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.expected_entries = expected_entries
        self.requester_id = requester_id
        self.remove_callback = remove_callback
        self.remove_button = CallbackButton[Self](
            self.remove,
            label="Удалить",
            style=DANGER,
        )
        self.add_item(self.remove_button)

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Удалить из очереди может только автор запроса.",
            ephemeral=True,
        )
        return False

    async def remove(self, interaction: Interaction) -> None:
        await MusicInteractionResponder(interaction).acknowledge_component()
        message = interaction.message
        if message is None:
            logger.debug("Queue undo component has no source message.")
            self.stop()
            return

        result = await self.remove_callback(
            self.guild_id,
            self.expected_entries,
            self.requester_id,
        )
        if not result.is_success or not result.data:
            self.stop()
            await message.edit(view=None)
            await interaction.followup.send(
                STALE_QUEUE_REQUEST_MESSAGE,
                ephemeral=True,
            )
            return

        removed = result.data
        self.stop()
        description = f"Треков: {len(removed)}" if len(removed) > 1 else ""
        embed = FeedbackUI.make_embed(
            title="Удалено из очереди",
            description=description,
            feedback_type=FeedbackType.SUCCESS,
        )
        await message.edit(
            embed=embed,
            view=None,
            delete_after=60,
        )

    @override
    async def on_error(
        self,
        interaction: Interaction,
        error: Exception,
        item: discord.ui.Item[Self],
        /,
    ) -> None:
        del item

        logger.error(
            "Queue undo interaction failed",
            exc_info=error,
        )
        self.stop()

        message = interaction.message
        if message is None:
            return

        try:
            await message.edit(view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


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
                f"{i}. {format_track_link(entry.track.title, entry.track.uri)}"
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
                value=format_track_link(track.title, track.uri),
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
