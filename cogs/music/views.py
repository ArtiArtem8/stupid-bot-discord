"""Views and interactive components for Music Cog."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Self, override

import discord
import mafic
from discord import Interaction, ui
from discord.abc import PrivateChannel
from discord.ext import commands
from discord.utils import format_dt

import config
from api.music import (
    MusicSession,
    QueueSnapshot,
    RepeatMode,
    TrackId,
)
from api.music.protocols import ControllerManagerProtocol
from framework import PRIMARY, BasePaginator, PaginationData
from utils import TextPaginator, truncate_text

from .ui import send_warning

if TYPE_CHECKING:
    from api.music import MusicPlayer, Track

logger = logging.getLogger(__name__)
MUSIC_PLAYER_EMOJIS = {
    # Bar Components
    "bar_left_full": "<:whitelineleftrounded:1447917292766626005>",
    "bar_mid_full": "<:whiteline:1447917290782724126>",
    "bar_right_full": "<:whitelinerightrounded:1447917295304446103>",
    "bar_left_empty": "<:graylineleftrounded:1447917287263830157>",
    "bar_mid_empty": "<:grayline:1447917284726411445>",
    "bar_right_empty": "<:graylinerightrounded:1447917289067515956>",
    # Controls
    "restart": "<:restart:1447913966939406366>",
    "back_10": "<:replay10:1447914002482200720>",
    "play": "<:play:1447913953345929311>",
    "pause": "<:pause:1447913941672919081>",
    "fwd_10": "<:forward10:1447913982408003595>",
    "skip": "<:skip:1447913916842901577>",
    "musical_note": "<:musicalnote:1447968776128565358>",
}


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

    async def get_page_count(self) -> int:
        return max(1, len(self._paginator.pages))

    def _build_paginator(self, snapshot: QueueSnapshot) -> TextPaginator:
        return TextPaginator(
            [f"{i}. [{t.title}]({t.uri})" for i, t in enumerate(snapshot.queue, 1)],
            page_size=self.page_size,
            max_length=config.MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

    def make_embed(self, page: int) -> discord.Embed:
        embed = discord.Embed(title="Очередь воспроизведения", color=config.Color.INFO)
        current = self.snapshot.current

        if current:
            embed.add_field(
                name="Сейчас играет",
                value=f"[{current.title}]({current.uri})",
                inline=False,
            )
            if current.artwork_url:
                embed.set_thumbnail(url=current.artwork_url)
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

        self.refresh_btn = ui.Button[Self](label="⭮", style=PRIMARY, row=1)
        self.refresh_btn.callback = self.refresh
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


class SessionPaginationAdapter(PaginationData):
    """Adapts music session history for the paginator."""

    def __init__(self, session: MusicSession, page_size: int = 15) -> None:
        self.session = session
        self.page_size = page_size
        self._paginator = self._build_paginator()

    async def get_page_count(self) -> int:
        return max(1, len(self._paginator.pages))

    def _build_paginator(self) -> TextPaginator:
        lines = [
            (
                f"{format_dt(t.timestamp, 'T')} • {i}. "
                f"{'~~' if t.skipped else ''}"
                f"[{truncate_text(t.title, 45)}]({t.uri})"
                f"{'~~' if t.skipped else ''} "
                f"{f'(<@{t.requester_id}>)' if t.requester_id else ''}"
            )
            for i, t in enumerate(self.session.tracks, 1)
        ]
        # Result: <timestamp> • <index>. <title> <requester_id>
        return TextPaginator(
            lines,
            page_size=self.page_size,
            max_length=config.MAX_EMBED_FIELD_LENGTH,
            separator="\n",
        )

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
        self, interaction: Interaction, button: ui.Button[Self]
    ) -> None:
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

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass


class TrackControllerManager(ControllerManagerProtocol):
    def __init__(self, bot: commands.Bot):
        """Initialize the TrackControllerManager.

        Args:
            bot: The bot instance.

        """
        self.bot = bot
        self.controllers: dict[int, TrackControllerView] = {}
        self._active_messages: dict[int, tuple[int, int]] = {}
        self._locks = defaultdict(asyncio.Lock)

    async def _safe_delete_message(self, channel_id: int, message_id: int):
        """Safely delete a message, handling missing channels/messages."""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.HTTPException:
                    return
            if isinstance(
                channel, (discord.ForumChannel, discord.CategoryChannel, PrivateChannel)
            ):
                return
            partial_msg = channel.get_partial_message(message_id)
            await partial_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.debug(
                "Failed to delete message %s in channel %s: %s",
                message_id,
                channel_id,
                e,
            )

    @override
    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
        track: Track,
    ):
        """Creates a new controller, replacing any existing one safely."""
        async with self._locks[guild_id]:
            logger.debug(f"Manager: Setup controller for guild {guild_id}")
            target_id = TrackId.from_track(track)

            if not await self._wait_for_sync(player, target_id, timeout=2.0):
                current_id = (
                    TrackId.from_track(player.current) if player.current else "None"
                )
                logger.debug(
                    "Manager: Aborting. Player state desynced. "
                    + f"Expected: {target_id}, Got: {current_id}",
                )
                return

            await self._cleanup_existing(guild_id)

            async def on_view_stop_callback(view_ref: TrackControllerView):
                await self.destroy_for_guild(guild_id, requesting_view=view_ref)

            view = TrackControllerView(
                user_id=user_id,
                player=player,
                guild_id=guild_id,
                track_id=target_id,
                on_stop_callback=on_view_stop_callback,
            )

            try:
                view.update_buttons_state()
                msg = await channel.send(
                    embed=view.make_embed(), view=view, silent=True
                )

                view.message = msg
                self.controllers[guild_id] = view
                self._active_messages[guild_id] = (channel.id, msg.id)  # pyright: ignore[reportAttributeAccessIssue]

                view.start_updater()
                logger.debug(f"Manager: Controller active for {target_id.id}")

            except Exception as e:
                logger.exception(f"Failed to send controller: {e}")
                view.stop()

    @override
    async def destroy_for_guild(
        self, guild_id: int, requesting_view: TrackControllerView | None = None
    ):
        """Destroys the controller for a guild.
        If requesting_view is provided, only destroys if current active view.
        """
        async with self._locks[guild_id]:
            current_view = self.controllers.get(guild_id)

            if requesting_view and current_view != requesting_view:
                logger.debug(
                    "Manager: Ignoring destroy request from stale view for guild %s",
                    guild_id,
                )
                return

            await self._cleanup_existing(guild_id)

    async def _cleanup_existing(self, guild_id: int):
        """Internal helper to clean up resources. Assumes lock is held."""
        controller = self.controllers.pop(guild_id, None)
        if controller:
            try:
                controller.stop()
            except Exception as e:
                logger.error(f"Error stopping controller: {e}")

        message_info = self._active_messages.pop(guild_id, None)
        if message_info:
            logger.debug("Manager: Cleaning up message for guild %s", guild_id)
            chan_id, msg_id = message_info
            try:
                await self._safe_delete_message(chan_id, msg_id)
            except Exception as e:
                logger.warning(f"Failed to delete message: {e}")

    async def _wait_for_sync(
        self, player: MusicPlayer, target_id: TrackId, timeout: float = 2.0
    ) -> bool:
        """Helper to wait for player state to match target track."""

        async def check():
            while True:
                if player.current and TrackId.from_track(player.current) == target_id:
                    return
                await asyncio.sleep(0.1)

        try:
            await asyncio.wait_for(check(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


type ButtonCallback = Callable[
    ["TrackControllerView", Interaction, ui.Button["TrackControllerView"]],
    Coroutine[Any, Any, None],
]


def handle_view_errors(func: ButtonCallback) -> ButtonCallback:
    """Decorator to handle exceptions in button callbacks.

    Catches mafic.PlayerNotConnected and mafic.PlayerException exceptions,
    stopping the view and calling the on_stop_callback if provided.
    Logs any unhandled exceptions.

    """

    async def wrapper(
        self: "TrackControllerView",
        interaction: Interaction,
        button: ui.Button["TrackControllerView"],
    ) -> None:
        try:
            await func(self, interaction, button)
        except (mafic.PlayerNotConnected, mafic.PlayerException):
            logger.warning("Player error in %s", func.__name__)
            self.stop()
            if self.on_stop_callback:
                await self.on_stop_callback(self)
        except Exception:
            logger.exception("Unhandled error in %s", func.__name__)
            self.stop()
            if self.on_stop_callback:
                await self.on_stop_callback(self)

    return wrapper


class TrackControllerView(ui.View):
    def __init__(
        self,
        *,
        user_id: int,
        player: MusicPlayer,
        guild_id: int,
        track_id: TrackId,
        on_stop_callback: Callable[[Self], Awaitable[None]] | None,
    ):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.player = player
        self.guild_id = guild_id
        self.track_id = track_id
        self.on_stop_callback = on_stop_callback

        self.message: discord.Message | None = None
        self._task: asyncio.Task[None] | None = None
        self.update_interval = 20
        self._running = True

        # State Cache
        self._is_paused_cache: bool = False
        self._pause_start_time: float | None = None
        self._frozen_position: int = 0
        self._max_pause_duration = 900
        self._last_update_time: float = 0
        self._min_update_delay: float = 1.0

    @override
    def stop(self):
        """Stops the updater loop and interaction."""
        logger.debug("Stopping %s", self.__class__.__name__)
        self._running = False
        if self._task:
            self._task.cancel()
        super().stop()

    def make_embed(self) -> discord.Embed:
        track = self.player.current
        if not track:
            return discord.Embed(title="Playback Stopped", color=config.Color.INFO)

        if self._is_paused_cache:
            pos = self._frozen_position
        else:
            pos = self.player.position or 0

        length = track.length
        pos = max(0, min(pos, length))

        bar = self._make_bar(pos, length)
        cur_min, cur_sec = divmod(pos // 1000, 60)
        rem_ms = length - pos
        rem_min, rem_sec = divmod(rem_ms // 1000, 60)

        description = f"`{cur_min}:{cur_sec:02d}` {bar} `-{rem_min}:{rem_sec:02d}`"

        e = discord.Embed(
            title=f"{MUSIC_PLAYER_EMOJIS['musical_note']} {track.title}",
            description=description,
            color=config.Color.INFO,
        )
        if track.artwork_url:
            e.set_thumbnail(url=track.artwork_url)
        user_url = self.player.guild.get_member(self.user_id)
        user_avatar = user_url.display_avatar if user_url else None

        e.set_footer(
            text="Бета-тестирование", icon_url=user_avatar.url if user_avatar else None
        )
        return e

    def _make_bar(self, pos: int, length: int, width: int = 10) -> str:
        if length <= 0:
            ratio = 0
        else:
            ratio = max(0, min(1, pos / length))

        filled_count = int(ratio * width)
        if ratio >= 1.0:
            filled_count = width

        empty_count = max(0, width - filled_count)

        start_cap = (
            MUSIC_PLAYER_EMOJIS["bar_left_full"]
            if filled_count > 0
            else MUSIC_PLAYER_EMOJIS["bar_left_empty"]
        )
        end_cap = (
            MUSIC_PLAYER_EMOJIS["bar_right_full"]
            if width > 0 and filled_count == width
            else MUSIC_PLAYER_EMOJIS["bar_right_empty"]
        )

        middle = (MUSIC_PLAYER_EMOJIS["bar_mid_full"] * filled_count) + (
            MUSIC_PLAYER_EMOJIS["bar_mid_empty"] * empty_count
        )
        return f"{start_cap}{middle}{end_cap}"

    def start_updater(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        """Background loop to update embed and check track state."""
        failure_count = 0
        MAX_FAILURES = 3

        try:
            while self._running:
                await asyncio.sleep(self.update_interval)

                current_track = self.player.current

                if not current_track:
                    failure_count += 1
                    if failure_count >= MAX_FAILURES:
                        logger.debug("View: Player empty. Requesting stop.")
                        self.stop()
                        if self.on_stop_callback:
                            await self.on_stop_callback(self)
                        return
                    continue

                current_id = TrackId.from_track(current_track)
                if current_id != self.track_id:
                    logger.debug(
                        "View: Track changed (%s -> %s). Requesting stop.",
                        self.track_id,
                        current_id,
                    )
                    self.stop()
                    if self.on_stop_callback:
                        await self.on_stop_callback(self)
                    return

                failure_count = 0

                is_paused = self.player.paused

                if is_paused:
                    if not self._is_paused_cache:
                        self._is_paused_cache = True
                        self._pause_start_time = time.monotonic()
                        self._frozen_position = self.player.position or 0
                        await self._safe_update(force=True)
                    else:
                        if self._pause_start_time and (
                            time.monotonic() - self._pause_start_time
                            > self._max_pause_duration
                        ):
                            logger.debug("View: Paused too long. Requesting stop.")
                            self.stop()
                            if self.on_stop_callback:
                                await self.on_stop_callback(self)
                            return
                else:
                    if self._is_paused_cache:
                        self._is_paused_cache = False
                        self._pause_start_time = None
                        await self._safe_update(force=True)
                    else:
                        await self._safe_update()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"View Loop Error: {e}")

    def update_buttons_state(self):
        for child in self.children:
            if isinstance(child, ui.Button) and child.custom_id == "btn_pause_resume":
                child.emoji = (
                    MUSIC_PLAYER_EMOJIS["play"]
                    if self.player.paused
                    else MUSIC_PLAYER_EMOJIS["pause"]
                )
                break

    async def _safe_update(self, force: bool = False):
        """Updates the message with rate limiting."""
        if not self.message:
            logger.debug("View: Message not found. Stopping.")
            self.stop()
            return

        now = time.monotonic()
        if not force and (now - self._last_update_time < self._min_update_delay):
            return

        try:
            self.update_buttons_state()
            await self.message.edit(embed=self.make_embed(), view=self)
            self._last_update_time = now
        except discord.NotFound:
            logger.debug("View: Message deleted externally. Stopping.")
            self.stop()
        except discord.HTTPException:
            pass

    async def _check_owner(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Not your controller.", ephemeral=True
            )
            return False
        return True

    @ui.button(
        emoji=MUSIC_PLAYER_EMOJIS["restart"],
        style=discord.ButtonStyle.secondary,
        custom_id="btn_restart",
    )
    @handle_view_errors
    async def restart(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        if self._is_paused_cache:
            self._frozen_position = 0
        await self.player.seek(0)
        await interaction.response.defer()
        await self._safe_update(force=True)

    @ui.button(
        emoji=MUSIC_PLAYER_EMOJIS["back_10"],
        style=discord.ButtonStyle.secondary,
        custom_id="btn_back10",
    )
    @handle_view_errors
    async def back10(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        pos = (
            self._frozen_position
            if self._is_paused_cache
            else (self.player.position or 0)
        )
        new = max(pos - 10000, 0)
        await self.player.seek(new)
        if self._is_paused_cache:
            self._frozen_position = new
        await interaction.response.defer()
        await self._safe_update(force=True)

    @ui.button(
        emoji=MUSIC_PLAYER_EMOJIS["pause"],
        style=discord.ButtonStyle.secondary,
        custom_id="btn_pause_resume",
    )
    @handle_view_errors
    async def pause_resume(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        if self.player.paused:
            await self.player.resume()
            self._is_paused_cache = False
        else:
            await self.player.pause()
            self._is_paused_cache = True
            self._frozen_position = self.player.position or 0
            self._pause_start_time = time.monotonic()
        await interaction.response.defer()
        await self._safe_update(force=True)

    @ui.button(
        emoji=MUSIC_PLAYER_EMOJIS["fwd_10"],
        style=discord.ButtonStyle.secondary,
        custom_id="btn_fwd10",
    )
    @handle_view_errors
    async def forward10(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        if not self.player.current:
            return
        pos = (
            self._frozen_position
            if self._is_paused_cache
            else (self.player.position or 0)
        )
        new = min(pos + 10000, self.player.current.length)
        await self.player.seek(new)
        if self._is_paused_cache:
            self._frozen_position = new
        await interaction.response.defer()
        await self._safe_update(force=True)

    @ui.button(
        emoji=MUSIC_PLAYER_EMOJIS["skip"],
        style=discord.ButtonStyle.secondary,
        custom_id="btn_skip",
    )
    @handle_view_errors
    async def skip(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        await self.player.skip()
        await interaction.response.defer()
