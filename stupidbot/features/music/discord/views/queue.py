"""Queue pagination views."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Self, override

import discord
from discord import Interaction

from stupidbot.common.text import TextPaginator
from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.emojis import RESTART_EMOJI
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import ack_component
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.pagination import (
    BasePaginator,
    CallbackButton,
    PaginationData,
)
from stupidbot.discord_ui.theme import Color
from stupidbot.features.music.discord.access import check_music_access
from stupidbot.features.music.discord.feedback import send_warning
from stupidbot.features.music.discord.presentation import format_track_link
from stupidbot.features.music.messages import MusicMessages
from stupidbot.features.music.playback.models import (
    QueueEntry,
    QueueSnapshot,
    RepeatMode,
)
from stupidbot.features.music.results import (
    MusicResult,
    MusicResultReason,
)
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import PresentationContext

logger = logging.getLogger(__name__)

type QueueRefreshCallback = Callable[[], Awaitable[QueueSnapshot | None]]
type QueuedEntriesRemoveCallback = Callable[
    [int, tuple[QueueEntry, ...], int],
    Awaitable[MusicResult[tuple[QueueEntry, ...]]],
]

STALE_QUEUE_REQUEST_MESSAGE = pgettext_noop(
    "stale_queue",
    "No tracks from this request are still waiting in the queue. They may have "
    "started playing or the queue may have changed.",
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
        context: PresentationContext,
    ) -> None:
        super().__init__(timeout=timeout)
        self.context = context
        self.messages = MusicMessages(catalog("music", context.language))
        self.guild_id = guild_id
        self.expected_entries = expected_entries
        self.requester_id = requester_id
        self.remove_callback = remove_callback
        self.remove_button = CallbackButton[Self](
            self.remove,
            label=self.messages.catalog.pgettext("ui", "Remove"),
            style=discord.ButtonStyle.danger,
        )
        self.add_item(self.remove_button)

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        await ack_component(interaction)
        voice_client = interaction.guild.voice_client if interaction.guild else None
        channel = voice_client.channel if voice_client is not None else None
        if not await check_music_access(interaction, channel):
            return False
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if interaction.user.id == self.requester_id:
            return True
        await FeedbackUI.send(
            interaction,
            description=messages.catalog.pgettext(
                "ui", "Only the requester can remove these tracks from the queue."
            ),
            ephemeral=True,
        )
        return False

    async def remove(self, interaction: Interaction) -> None:
        await ack_component(interaction)
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if not await check_component_access(interaction):
            return
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
        if not result.is_success and result.reason is MusicResultReason.RECOVERING:
            await interaction.followup.send(
                messages.failure(MusicResultReason.RECOVERING), ephemeral=True
            )
            return
        if not result.is_success or not result.data:
            self.stop()
            await message.edit(view=None)
            await interaction.followup.send(
                messages.catalog.pgettext("stale_queue", STALE_QUEUE_REQUEST_MESSAGE),
                ephemeral=True,
            )
            return

        removed = result.data
        self.stop()
        description = (
            self.messages.track_count(count=len(removed)) if len(removed) > 1 else ""
        )
        embed = FeedbackUI.make_embed(
            title=self.messages.catalog.pgettext("ui", "Removed from the queue"),
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

    def __init__(
        self,
        snapshot: QueueSnapshot,
        page_size: int = 20,
        *,
        context: PresentationContext,
    ) -> None:
        self.snapshot = snapshot
        self.messages = MusicMessages(catalog("music", context.language))
        self.page_size = page_size
        self._paginator = self._build_paginator(snapshot)

    def update_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.snapshot = snapshot
        self._paginator = self._build_paginator(snapshot)

    @override
    async def get_page_count(self) -> int:
        return max(1, len(self._paginator.pages))

    def _build_paginator(self, snapshot: QueueSnapshot) -> TextPaginator:
        lines: list[str] = []
        for index, entry in enumerate(snapshot.queue, 1):
            prefix = f"{index}. "
            link = format_track_link(
                entry.track.title,
                entry.track.uri,
                max_length=MAX_EMBED_FIELD_LENGTH - len(prefix),
            )
            lines.append(f"{prefix}{link}")
        return TextPaginator(
            lines,
            page_size=self.page_size,
            max_length=MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

    @override
    def make_embed(self, page: int) -> discord.Embed:
        embed = discord.Embed(
            title=self.messages.catalog.pgettext("ui", "Playback queue"),
            color=Color.INFO,
        )
        current = self.snapshot.current

        if current:
            track = current.track
            embed.add_field(
                name=self.messages.catalog.pgettext("ui", "Now playing"),
                value=format_track_link(track.title, track.uri),
                inline=False,
            )
            if track.artwork_url:
                embed.set_thumbnail(url=track.artwork_url)
        else:
            embed.description = self.messages.catalog.pgettext(
                "ui", "Nothing is playing."
            )

        if 0 <= page < len(self._paginator.pages):
            embed.add_field(
                name=self.messages.catalog.pgettext("ui", "Up next"),
                value=self._paginator.pages[page],
                inline=False,
            )

        repeat_labels = {
            RepeatMode.OFF: self.messages.catalog.pgettext("ui", "off"),
            RepeatMode.QUEUE: self.messages.catalog.pgettext("repeat_mode", "queue"),
            RepeatMode.TRACK: self.messages.catalog.pgettext("repeat_mode", "track"),
        }
        total_pages = max(1, len(self._paginator.pages))
        embed.set_footer(
            text=self.messages.queue_footer(
                page=page + 1,
                pages=total_pages,
                count=self._paginator.total_items,
                repeat=repeat_labels[self.snapshot.repeat_mode],
            )
        )
        return embed

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        await send_warning(
            interaction,
            messages.catalog.pgettext(
                "ui", "Please leave this alone; it is not your message."
            ),
            ephemeral=True,
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
            self.refresh, emoji=RESTART_EMOJI, row=1
        )
        self.add_item(self.refresh_btn)

    async def refresh(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if not await check_component_access(interaction):
            return
        new_data = await self.refresh_callback()
        if new_data:
            self.adapter.update_snapshot(new_data)
            self.page = 0
            await self._update_view(interaction)
        else:
            await self.close(interaction)
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "Could not refresh the queue"),
                ephemeral=True,
            )
