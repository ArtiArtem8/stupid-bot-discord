"""Session history views."""

from __future__ import annotations

from typing import Self, override

import discord
from discord import Interaction, ui
from discord.utils import format_dt

from stupidbot.common.text import TextPaginator, truncate_text
from stupidbot.discord_ui.access import check_component_access
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.pagination import BasePaginator, PaginationData
from stupidbot.discord_ui.theme import Color
from stupidbot.features.music.discord.feedback import send_warning
from stupidbot.features.music.discord.presentation import format_track_link
from stupidbot.features.music.messages import MusicMessages
from stupidbot.features.music.runtime.session import MusicSession
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import PresentationContext


class SessionPaginationAdapter(PaginationData):
    """Adapts music session history for the paginator."""

    def __init__(
        self,
        session: MusicSession,
        page_size: int = 15,
        *,
        context: PresentationContext,
    ) -> None:
        self.session = session
        self.messages = MusicMessages(catalog("music", context.language))
        self.page_size = page_size
        self._paginator = self._build_paginator()

    @override
    async def get_page_count(self) -> int:
        return max(1, len(self._paginator.pages))

    def _build_paginator(self) -> TextPaginator:
        lines: list[str] = []
        for index, track in enumerate(self.session.tracks, 1):
            timestamp = format_dt(track.end_timestamp, "T")
            marker = "~~" if track.skipped else ""
            link = format_track_link(truncate_text(track.title, 45), track.uri)
            requester = f"(<@{track.requester_id}>)" if track.requester_id else ""
            lines.append(f"{timestamp} • {index}. {marker}{link}{marker} {requester}")
        return TextPaginator(
            lines,
            page_size=self.page_size,
            max_length=MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

    @override
    def make_embed(self, page: int) -> discord.Embed:
        description = (
            self._paginator.pages[page]
            if 0 <= page < len(self._paginator.pages)
            else self.messages.catalog.pgettext("ui", "Empty")
        )

        embed = discord.Embed(
            title=self.messages.catalog.pgettext("ui", "Full history"),
            color=Color.INFO,
            timestamp=self.session.start_time,
            description=description,
        )

        total_pages = max(1, len(self._paginator.pages))
        embed.set_footer(
            text=self.messages.history_footer(
                page=page + 1, pages=total_pages, count=self._paginator.total_items
            )
        )
        return embed

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        await send_warning(
            interaction,
            messages.catalog.pgettext("ui", "How did you manage that?"),
            ephemeral=True,
        )


class SessionSummaryView(ui.View):
    """Simplified view for session summaries."""

    def __init__(
        self,
        *,
        session: MusicSession,
        context: PresentationContext,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.view_full_button.label = catalog("music", context.language).pgettext(
            "ui", "History"
        )
        self.session = session
        self.message: discord.Message | None = None

    @ui.button(label=pgettext_noop("ui", "History"), style=discord.ButtonStyle.primary)
    async def view_full_button(
        self, interaction: Interaction, _: ui.Button[Self]
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if not await check_component_access(interaction):
            return
        total_tracks = len(self.session.tracks)
        if total_tracks == 0:
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "There are no tracks in this session."),
                ephemeral=True,
            )
            return

        adapter = SessionPaginationAdapter(self.session, context=context)
        paginator = BasePaginator(
            data=adapter, user_id=interaction.user.id, show_first_last=False
        )
        await paginator.prepare()
        await paginator.send(interaction, ephemeral=True, silent=True)

    @override
    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass
