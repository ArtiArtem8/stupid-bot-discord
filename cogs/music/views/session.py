"""Session history views."""

from __future__ import annotations

from typing import Self, override

import discord
from discord import Interaction, ui
from discord.utils import format_dt

import config
from api.music.models import MusicSession
from framework.authorization import check_component_access
from framework.pagination import BasePaginator, PaginationData
from utils.text_utils import TextPaginator, truncate_text

from ..feedback import send_warning
from ..presentation import format_track_link


class SessionPaginationAdapter(PaginationData):
    """Adapts music session history for the paginator."""

    def __init__(self, session: MusicSession, page_size: int = 15) -> None:
        self.session = session
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
            max_length=config.MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

    @override
    def make_embed(self, page: int) -> discord.Embed:
        description = (
            self._paginator.pages[page]
            if 0 <= page < len(self._paginator.pages)
            else "Пусто"
        )

        embed = discord.Embed(
            title="Полная история",
            color=config.Color.INFO,
            timestamp=self.session.start_time,
            description=description,
        )

        total_pages = max(1, len(self._paginator.pages))
        embed.set_footer(
            text=f"Стр. {page + 1}/{total_pages} • {self._paginator.total_items} всего"
        )
        return embed

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        await send_warning(interaction, "Как ты этого добился?", ephemeral=True)


class SessionSummaryView(ui.View):
    """Simplified view for session summaries."""

    def __init__(self, *, session: MusicSession, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.session = session
        self.message: discord.Message | None = None

    @ui.button(label="История", style=discord.ButtonStyle.primary)
    async def view_full_button(
        self, interaction: Interaction, _: ui.Button[Self]
    ) -> None:
        if not await check_component_access(interaction):
            return
        total_tracks = len(self.session.tracks)
        if total_tracks == 0:
            await send_warning(interaction, "В этой сессии нет треков.", ephemeral=True)
            return

        adapter = SessionPaginationAdapter(self.session)
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
