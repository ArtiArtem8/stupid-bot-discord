"""Views and interactive components for Music Cog."""

from __future__ import annotations

import asyncio
import logging
import time
from math import ceil
from typing import Awaitable, Callable, Self

import discord
from discord import Interaction, ui
from discord.abc import PrivateChannel
from discord.ext import commands
from discord.utils import format_dt

import config
from api.music import (
    MusicPlayer,
    MusicSession,
    QueueSnapshot,
    RepeatMode,
    Track,
    TrackId,
)
from framework import PRIMARY, BasePaginator, PaginationData
from utils import truncate_text

from .ui import send_warning

LOGGER = logging.getLogger(__name__)
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

    def update_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.snapshot = snapshot

    async def get_page_count(self) -> int:
        upcoming_count = len(self.snapshot.queue)
        return max((upcoming_count + self.page_size - 1) // self.page_size, 1)

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

        queue_list = list(self.snapshot.queue)
        start = page * self.page_size
        end = min(len(queue_list), start + self.page_size)

        if queue_list and start < len(queue_list):
            lines = [
                f"{idx + 1}. [{track.title}]({track.uri})"
                for idx, track in enumerate(queue_list[start:end], start=start)
            ]
            embed.add_field(
                name="Далее",
                value="\n".join(lines),
                inline=False,
            )

        repeat_str = (
            "выкл."
            if self.snapshot.repeat_mode is RepeatMode.OFF
            else self.snapshot.repeat_mode.value
        )
        total_pages = max((len(queue_list) + self.page_size - 1) // self.page_size, 1)

        embed.set_footer(
            text=(
                f"Стр. {page + 1}/{total_pages} • "
                f"В очереди: {len(queue_list)} • "
                f"Повтор: {repeat_str}"
            )
        )
        return embed

    async def on_unauthorized(self, interaction: Interaction) -> None:
        await send_warning(
            interaction, "Попрошу не трогать, вы не диджей.", ephemeral=True
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

    async def get_page_count(self) -> int:
        return max(1, ceil(len(self.session.tracks) / self.page_size))

    def make_embed(self, page: int) -> discord.Embed:
        start = page * self.page_size
        end = min(len(self.session.tracks), start + self.page_size)

        embed = discord.Embed(
            title="Полная история",
            color=config.Color.INFO,
            timestamp=self.session.start_time,
        )

        lines: list[str] = []
        for idx, track in enumerate(self.session.tracks[start:end], start=start + 1):
            status = "~~" if track.skipped else ""
            track_str = f"[{truncate_text(track.title, width=45, placeholder='...')}]({track.uri})"
            requester_str = f"(<@{track.requester_id}>)" if track.requester_id else ""
            lines.append(
                f"{format_dt(track.timestamp, style='T')} • {idx}. {status}{track_str}{status} {requester_str}"
            )

        embed.description = "\n".join(lines) if lines else "Пусто"
        total_pages = max(1, ceil(len(self.session.tracks) / self.page_size))
        embed.set_footer(
            text=f"Стр. {page + 1}/{total_pages} • {len(self.session.tracks)} всего"
        )
        return embed

    async def on_unauthorized(self, interaction: Interaction) -> None:
        await send_warning(
            interaction, "Попрошу не трогать, вы не диджей.", ephemeral=True
        )


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
        await paginator.send(interaction, ephemeral=True, silent=True)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass


class TrackControllerManager:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.controllers: dict[int, TrackControllerView] = {}
        self._active_messages: dict[int, tuple[int, int]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    async def _safe_delete_message(self, channel_id: int, message_id: int):
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
                LOGGER.debug(
                    "Ignoring message deletion for channel type %s", type(channel)
                )
                return
            partial_msg = channel.get_partial_message(message_id)
            await partial_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        except discord.HTTPException:
            pass

    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
        track: Track,
    ):
        async with self._get_lock(guild_id):
            LOGGER.debug(f"Manager: Setup controller for guild {guild_id}")

            target_id = TrackId.from_track(track)

            async def wait_for_sync():
                for _ in range(10):
                    if (
                        player.current
                        and TrackId.from_track(player.current) == target_id
                    ):
                        return True
                    await asyncio.sleep(0.2)
                return False

            if not await wait_for_sync():
                LOGGER.debug(
                    f"Manager: Aborting. Player state desynced. "
                    f"Expected: {target_id}, Got: {TrackId.from_track(player.current) if player.current else 'None'}"
                )
                return

            if guild_id in self.controllers:
                self.controllers[guild_id].stop()
                del self.controllers[guild_id]

            if guild_id in self._active_messages:
                old_chan, old_msg = self._active_messages.pop(guild_id)
                await self._safe_delete_message(old_chan, old_msg)

            view = TrackControllerView(
                user_id=user_id,
                player=player,
                manager=self,
                guild_id=guild_id,
                track_id=target_id,
            )

            try:
                view.update_buttons_state()
                msg = await channel.send(
                    embed=view.make_embed(), view=view, silent=True
                )

                view.message = msg
                self.controllers[guild_id] = view
                self._active_messages[guild_id] = (channel.id, msg.id)  # type: ignore
                view.start_updater()
                LOGGER.debug(f"Manager: Controller active for {target_id.id}")

            except Exception as e:
                LOGGER.exception(f"Failed to send controller: {e}")

    async def destroy_for_guild(self, guild_id: int):
        async with self._get_lock(guild_id):
            if guild_id in self.controllers:
                self.controllers[guild_id].stop()
                del self.controllers[guild_id]

            if guild_id in self._active_messages:
                chan_id, msg_id = self._active_messages.pop(guild_id)
                await self._safe_delete_message(chan_id, msg_id)

    async def destroy_specific(self, guild_id: int, user_id: int):
        # Redirect to main destroy logic to ensure lock safety
        await self.destroy_for_guild(guild_id)


class TrackControllerView(ui.View):
    def __init__(
        self,
        *,
        user_id: int,
        player: MusicPlayer,
        manager: TrackControllerManager,
        guild_id: int,
        track_id: TrackId,
    ):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.track_id = track_id
        self.player = player
        self.manager = manager
        self.guild_id = guild_id
        self.message: discord.Message | None = None
        self._task: asyncio.Task[None] | None = None
        self.update_interval = 20

        # State
        self._is_paused_cache: bool = False
        self._pause_start_time: float | None = None
        self._frozen_position: int = 0
        self._max_pause_duration = 900
        self._last_update_time: float = 0
        self._min_update_delay: float = 1.0
        self._running = True

    def _stop(self):
        """Stops the updater loop safely."""
        self._running = False
        if self._task:
            self._task.cancel()
        self.stop()

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
        failure_count = 0
        MAX_FAILURES = 3
        try:
            while self._running:
                await asyncio.sleep(self.update_interval)
                current_track = self.player.current
                # Check 1: Is track still playing?
                if not current_track:
                    failure_count += 1
                    if failure_count >= MAX_FAILURES:
                        LOGGER.debug("Player empty for too long. Destroying.")
                        await self.manager.destroy_for_guild(self.guild_id)
                        return
                    continue

                current_id = TrackId.from_track(current_track)
                if current_id != self.track_id:
                    LOGGER.debug(
                        f"Track changed ({self.track_id} -> {current_id}). Destroying."
                    )
                    await self.manager.destroy_for_guild(self.guild_id)
                    return

                failure_count = 0
                if self.player.paused:
                    if not self._is_paused_cache:
                        self._is_paused_cache = True
                        self._pause_start_time = time.monotonic()
                        self._frozen_position = self.player.position or 0
                        await self._safe_update(force=True)
                    else:
                        # Timeout Check
                        if self._pause_start_time and (
                            time.monotonic() - self._pause_start_time
                            > self._max_pause_duration
                        ):
                            await self.manager.destroy_for_guild(self.guild_id)
                            return
                else:
                    if self._is_paused_cache:
                        self._is_paused_cache = False
                        self._pause_start_time = None
                        await self._safe_update(force=True)
                    else:
                        await self._safe_update()

        except asyncio.CancelledError:
            return

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
        now = time.monotonic()
        if not force and (now - self._last_update_time < self._min_update_delay):
            return

        if self.message:
            try:
                self.update_buttons_state()
                await self.message.edit(embed=self.make_embed(), view=self)
                self._last_update_time = now
            except discord.NotFound:
                # Message was deleted externally. Stop updater.
                self._stop()
            except discord.HTTPException:
                pass

    # --- BUTTONS ---
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
    async def skip(self, interaction: Interaction, _: ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        await self.player.skip()
        await interaction.response.defer()
