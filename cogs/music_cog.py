"""Music playback with Lavalink integration.

Provides:
- Playing music from various sources
- Queue management
- Playback control (pause, resume, skip, stop)
- Volume control
- Voice channel management
- Auto-leave when channel is empty for too long

Requirements:
    - Lavalink server running
    - Environment variables: LAVALINK_HOST, LAVALINK_PORT, LAVALINK_PASSWORD
"""

import logging
import time
from datetime import timedelta
from math import ceil
from typing import (
    Any,
    Final,
    Self,
    TypedDict,
    override,
)

import discord
from discord import Interaction, Member, app_commands
from discord.channel import VocalGuildChannel
from discord.ext import commands, tasks

import config
from api import (
    LavalinkVoiceClient,
    MusicAPI,
    MusicResultStatus,
    MusicSession,
    Player,
    PlayList,
    RepeatMode,
    Track,
    VoiceCheckResult,
    VoiceJoinResult,
)
from framework import BaseCog, FeedbackType, FeedbackUI, handle_errors
from utils import truncate_text

LOGGER = logging.getLogger("MusicCog")
PAGE_SIZE = 20
MAX_SELECT_OPTIONS = 25
EMBED_SAFE_DESC_LIMIT = 3800


class EmptyTimerInfo(TypedDict):
    timestamp: float
    reason: str | None


VOICE_MESSAGES = {
    VoiceCheckResult.ALREADY_CONNECTED: "Уже подключён к {0}",
    VoiceCheckResult.CHANNEL_EMPTY: "Голосовой канал {0} пуст!",
    VoiceCheckResult.CONNECTION_FAILED: "Ошибка подключения к {0}",
    VoiceCheckResult.INVALID_CHANNEL_TYPE: "Неверный тип голосового канала",
    VoiceCheckResult.MOVED_CHANNELS: "Переместился {1} -> {0}",
    VoiceCheckResult.SUCCESS: "Успешно подключился к {0}",
    VoiceCheckResult.USER_NOT_IN_VOICE: "Вы должны быть в голосовом канале!",
    VoiceCheckResult.USER_NOT_MEMBER: "Неверный тип пользователя",
}


def _format_voice_result_message(
    result: VoiceCheckResult,
    to_channel: VocalGuildChannel,
    from_channel: VocalGuildChannel | None,
) -> str:
    """Helper to format the message based on the result and data."""
    msg = VOICE_MESSAGES.get(result, "Неизвестная ошибка")
    return msg.format(
        to_channel.mention, from_channel.mention if from_channel else None
    )


async def _send_error(interaction: Interaction, message: str) -> None:
    return await FeedbackUI.send(
        interaction,
        feedback_type=FeedbackType.ERROR,
        description=message,
        delete_after=600,
    )


MAX_TIMEDELTA_DAYS = 999_999_999


def _format_duration(ms: int | float) -> str:
    """Helper to convert milliseconds to timedelta stripping microseconds."""
    try:
        total = timedelta(seconds=ms / 1_000.0)  # convert to seconds to avoid overflow
    except OverflowError:
        total = timedelta(days=min(MAX_TIMEDELTA_DAYS, ms // 86_400_000))
    except ValueError:
        return "NaN"
    total -= timedelta(microseconds=total.microseconds)
    if total.days >= MAX_TIMEDELTA_DAYS - 1_000_000:
        return "∞"
    if total.days >= 14:
        return str(total.days) + " days"
    return str(total)


class SessionSummaryView(discord.ui.View):
    """Simplified view for session summaries with ephemeral full details."""

    def __init__(self, *, session: MusicSession, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.session = session
        self.message: discord.Message | None = None

    @discord.ui.button(label="История", style=discord.ButtonStyle.primary)
    async def view_full_button(
        self, interaction: Interaction, button: discord.ui.Button[Self]
    ) -> None:
        """Send full session details as ephemeral paginated message."""
        total_tracks = len(self.session.tracks)

        if total_tracks == 0:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="В этой сессии нет треков.",
                ephemeral=True,
            )
            return
        paginator = SessionDetailsPaginator(
            user_id=interaction.user.id, session=self.session
        )
        await paginator.send(interaction)

    @override
    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                LOGGER.debug("Failed to edit message view: %s", self.message.id)
                pass


class SessionDetailsPaginator(discord.ui.View):
    """Ephemeral paginator for full session details."""

    def __init__(
        self, user_id: int, session: MusicSession, *, timeout: float = 300.0
    ) -> None:
        super().__init__(timeout=timeout)
        self.user_id: Final = user_id
        self.session: Final = session
        self.page_size: Final = 15
        self.page = 0

        # Setup navigation buttons
        self.prev_btn = discord.ui.Button[Self](
            label="◀", style=discord.ButtonStyle.secondary, disabled=True
        )
        self.next_btn = discord.ui.Button[Self](
            label="▶", style=discord.ButtonStyle.secondary
        )
        self.close_btn = discord.ui.Button[Self](
            label="✕", style=discord.ButtonStyle.danger, row=0
        )

        self.prev_btn.callback = self.prev_page
        self.next_btn.callback = self.next_page
        self.close_btn.callback = self.close

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.close_btn)
        self._update_buttons()

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        """Ensure only the command user can interact."""
        if interaction.user.id != self.user_id:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Попрошу не трогать, вы не диджей.",
                ephemeral=True,
            )
            return False
        return True

    def _total_pages(self) -> int:
        """Calculate total number of pages."""
        return max(1, ceil(len(self.session.tracks) / self.page_size))

    def _update_buttons(self) -> None:
        """Update button states based on current page."""
        total_pages = self._total_pages()
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= total_pages - 1

    def _make_embed(self) -> discord.Embed:
        """Generate embed for current page."""
        start = self.page * self.page_size
        end = min(len(self.session.tracks), start + self.page_size)

        embed = discord.Embed(
            title="Полная история",
            color=config.Color.INFO,
            timestamp=self.session.start_time,
        )

        lines: list[str] = []
        for idx, track in enumerate(self.session.tracks[start:end], start=start + 1):
            status = "~~" if track.skipped else ""
            track_str = f"[{truncate_text(track.title, width=35, placeholder='...')}]({track.uri})"
            requester_str = f"(<@{track.requester_id}>)" if track.requester_id else ""
            lines.append(f"{idx}. {status}{track_str}{status} {requester_str}")

        embed.description = "\n".join(lines) if lines else "Пусто"

        embed.set_footer(
            text=f"Стр. {self.page + 1}/{self._total_pages()} • "
            f"{len(self.session.tracks)} всего"
        )

        return embed

    async def send(self, interaction: Interaction) -> None:
        """Send initial paginated message."""
        await interaction.response.send_message(
            embed=self._make_embed(), view=self, ephemeral=True
        )

    async def _update_view(self, interaction: Interaction) -> None:
        """Update the view with current page."""
        self._update_buttons()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    async def prev_page(self, interaction: Interaction) -> None:
        """Navigate to previous page."""
        self.page = max(0, self.page - 1)
        await self._update_view(interaction)

    async def next_page(self, interaction: Interaction) -> None:
        """Navigate to next page."""
        self.page = min(self.page + 1, self._total_pages() - 1)
        await self._update_view(interaction)

    async def close(self, interaction: Interaction) -> None:
        """Close the paginator."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @override
    async def on_timeout(self) -> None:
        """Handle view timeout."""
        self.stop()


class MusicCog(BaseCog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.music_api = MusicAPI(bot)
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}

    @property
    def node(self) -> Any | None:
        """Expose node for LavalinkVoiceClient (accessed via getattr)."""
        return self.music_api.node

    @override
    async def cog_unload(self) -> None:
        if hasattr(self, "auto_leave_monitor") and self.auto_leave_monitor.is_running():
            self.auto_leave_monitor.cancel()

        await self.music_api.cleanup()

    @override
    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.music_api.initialize()

        self.auto_leave_monitor.start()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.music_api.initialize()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Monitor voice state changes for auto-leave feature."""
        if not self.bot.user:
            return

        guild = member.guild
        if not guild.voice_client or not isinstance(
            guild.voice_client, LavalinkVoiceClient
        ):
            return
        self.logger.debug("Voice state update: %s -> %s", before, after)
        bot_channel = guild.voice_client.channel
        self.logger.debug("Bot channel: %s", bot_channel)
        affected_channels: set[VocalGuildChannel] = set()
        if before.channel == bot_channel:
            affected_channels.add(bot_channel)
        if after.channel == bot_channel:
            affected_channels.add(bot_channel)

        if before.channel == bot_channel == after.channel and (
            before.deaf != after.deaf or before.self_deaf != after.self_deaf
        ):
            affected_channels.add(bot_channel)

        for channel in affected_channels:
            await self._update_channel_timer(guild.id, channel)

    async def _update_channel_timer(
        self, guild_id: int, channel: VocalGuildChannel
    ) -> None:
        """Update the empty channel timer for a specific guild."""
        human_members = [m for m in channel.members if not m.bot]

        effectively_empty = False
        empty_reason: str | None = None

        if len(human_members) == 0:
            effectively_empty = True
            empty_reason = "empty"
        else:
            all_deafened = all(
                (m.voice.self_deaf or m.voice.deaf)
                for m in human_members
                if m.voice is not None
            )
            if all_deafened:
                effectively_empty = True
                empty_reason = "all_deafened"

        if effectively_empty:
            if guild_id not in self.empty_channel_timers:
                self.logger.info(
                    "Channel %s in guild %s is effectively empty (%s). Starting timer.",
                    channel.name,
                    guild_id,
                    empty_reason,
                )
                self.empty_channel_timers[guild_id] = EmptyTimerInfo(
                    timestamp=time.monotonic(),
                    reason=empty_reason,
                )
        else:
            if guild_id in self.empty_channel_timers:
                self.logger.info(
                    "Channel %s in guild %s is no longer empty. Cancelling timer.",
                    channel.name,
                    guild_id,
                )
                self.empty_channel_timers.pop(guild_id, None)

    @tasks.loop(seconds=config.MUSIC_AUTO_LEAVE_CHECK_INTERVAL)
    async def auto_leave_monitor(self) -> None:
        """Check if bot should leave empty channels."""
        try:
            current_time = time.monotonic()
            timeout_duration = config.MUSIC_AUTO_LEAVE_TIMEOUT

            for guild_id, info in list(self.empty_channel_timers.items()):
                if current_time - info["timestamp"] > timeout_duration:
                    await self._auto_leave_guild(guild_id, info["reason"])
        except Exception as e:
            self.logger.exception("Error in auto_leave_monitor: %s", e)

    async def _auto_leave_guild(self, guild_id: int, reason: str | None) -> None:
        """Handle the actual leaving logic."""
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            self.empty_channel_timers.pop(guild_id, None)
            return

        try:
            self.logger.info(
                "Auto-leaving guild %s (%s) due to inactivity (%s).",
                guild.name,
                guild_id,
                reason,
            )
            await self.music_api.leave(guild)
            self.empty_channel_timers.pop(guild_id, None)
        except Exception as e:
            self.logger.error("Failed to auto-leave guild %s: %s", guild_id, e)

    @auto_leave_monitor.before_loop
    async def before_auto_leave_monitor(self) -> None:
        await self.bot.wait_until_ready()

    async def _handle_failed_join(
        self,
        interaction: Interaction,
        channel: VocalGuildChannel,
        voice_join_result: VoiceJoinResult,
    ) -> None:
        """Handle failed join attempts."""
        result, from_channel = voice_join_result
        msg = _format_voice_result_message(result, channel, from_channel)
        if result.status is not MusicResultStatus.SUCCESS:
            if result.status is MusicResultStatus.ERROR:
                await _send_error(interaction, msg)
                return
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=msg,
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_music_session_end(
        self, guild_id: int, session: MusicSession, channel_id: int
    ) -> None:
        """Handle music session end event with concise summary."""
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        if not session.tracks:
            return

        total_tracks = len(session.tracks)
        played_tracks = sum(1 for t in session.tracks if not t.skipped)
        skipped_tracks = total_tracks - played_tracks

        embed = discord.Embed(
            title="Сессия закончена",
            color=config.Color.INFO,
            timestamp=session.start_time,
        )

        embed.add_field(
            name="В общем:",
            value=(
                f"**Всего:** {total_tracks} шт." + f"(скипов: {skipped_tracks})\n"
                if skipped_tracks
                else f"**Диджеев:** {len(session.participants)} чел."
            ),
            inline=True,
        )

        preview_tracks = session.tracks[-15:]
        track_preview: list[str] = []
        for t in preview_tracks:
            status = "~~" if t.skipped else ""
            track_str = f"[{truncate_text(t.title, 35, placeholder='...')}]({t.uri})"
            track_preview.append(f"{status}{track_str}{status}\n")
        total_preview = "".join(reversed(track_preview))
        if len(total_preview) >= 1024:
            total_preview = total_preview[:1022].rsplit("\n", 1)[0] + "\n"
        embed.add_field(name="Недавние треки:", value=total_preview, inline=False)
        view = SessionSummaryView(session=session, timeout=300.0)
        try:
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
        except Exception:
            self.logger.exception(
                "Failed to send session summary to channel %s", channel_id
            )

    @app_commands.command(name="join", description="Подключиться к голосовому каналу")
    @app_commands.guild_only()
    @handle_errors()
    async def join(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        if not isinstance(interaction.user, Member):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Вы не участник сервера.",
                ephemeral=True,
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Вы должны быть в голосовом канале!",
                ephemeral=True,
            )
            return

        channel = interaction.user.voice.channel
        result, from_channel = await self.music_api.join(guild, channel)
        is_error = result.status is MusicResultStatus.ERROR
        msg = _format_voice_result_message(result, channel, from_channel)

        self.logger.log(
            logging.ERROR if is_error else logging.INFO,
            "Join command: %s for user %s in %s",
            result.name,
            interaction.user,
            guild.id,
        )

        if result.status is not MusicResultStatus.SUCCESS:
            await self._handle_failed_join(interaction, channel, (result, from_channel))
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description=msg,
            delete_after=60,
        )

    @app_commands.command(
        name="play",
        description="Воспроизведение музыки с YT, SoundCloud, YaMusic и VK",
    )
    @app_commands.describe(
        query="Название трека или URL",
        ephemeral="Скрывает ваше сообщение от всех (если True)",
    )
    @app_commands.guild_only()
    @handle_errors()
    async def play(
        self,
        interaction: Interaction,
        *,
        query: str,
        ephemeral: bool = False,
    ) -> None:
        guild = await self._require_guild(interaction)

        if not isinstance(interaction.user, Member):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description="Вы не участник сервера.",
                ephemeral=True,
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Вы должны быть в голосовом канале!",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=ephemeral)
        channel = interaction.user.voice.channel
        result = await self.music_api.play(
            guild,
            channel,
            query,
            interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        if not result.is_success:
            if isinstance(result.data, tuple):
                await self._handle_failed_join(interaction, channel, result.data)
            else:
                await _send_error(interaction, result.message or "Неизвестная ошибка.")
            return

        data = result.data
        if not data:
            await _send_error(interaction, "Ошибка: данных нет.")
            return

        duration_ms = await self.music_api.get_queue_duration(guild.id)

        try:
            delay = timedelta(seconds=duration_ms / 1000 + 60)
            delay_sec = delay.total_seconds()
        except Exception:
            delay_sec = float("inf")

        match data:
            case {"type": "playlist", "playlist": playlist}:
                await self._handle_playlist_result(
                    interaction, playlist, delete_after=min(3660, delay_sec)
                )
            case {"type": "track", "track": track, "playing": playing}:
                await self._handle_track_result(
                    interaction,
                    track,
                    playing,
                    delete_after=min(660, delay_sec),
                )
            case _:
                await _send_error(interaction, "Ошибка: неизвестный формат данных.")

    async def _handle_track_result(
        self,
        interaction: Interaction,
        track: Track,
        is_playing: bool,
        delete_after: float,
    ) -> None:
        try:
            embed = discord.Embed(
                title="Сейчас играет" if not is_playing else "Добавлено в очередь",
                description=f"[{track.title}]({track.uri})",
                color=config.Color.INFO,
            )
            if url := track.artworkUrl:
                embed.set_thumbnail(url=url)

            embed.add_field(
                name="Длительность",
                value=_format_duration(track.length),
            )
            embed.set_footer(
                text=f"Запросил: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )

            await FeedbackUI.send(interaction, embed=embed, delete_after=delete_after)
        except Exception as e:
            self.logger.exception("Error handling track result: %s", e)
            await _send_error(interaction, "Ошибка при отображении трека.")

    async def _handle_playlist_result(
        self, interaction: Interaction, playlist: PlayList, delete_after: float
    ) -> None:
        try:
            embed = discord.Embed(
                title=f"Добавлен плейлист **{playlist.name}**",
                description=(f"Треков: {len(playlist.tracks)} шт."),
                color=config.Color.INFO,
            )
            if playlist.tracks and (url := playlist.tracks[0].artworkUrl):
                embed.set_thumbnail(url=url)
            embed.add_field(
                name="Общая длительность",
                value=_format_duration(sum(track.length for track in playlist.tracks)),
            )
            embed.set_footer(
                text=f"Запросил: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )
            await FeedbackUI.send(interaction, embed=embed, delete_after=delete_after)
        except Exception as e:
            self.logger.exception("Error handling playlist result: %s", e)
            await _send_error(interaction, "Ошибка при отображении плейлиста.")

    @app_commands.command(
        name="stop", description="Остановить воспроизведение и очистить очередь"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def stop(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        result = await self.music_api.stop_player(
            guild.id,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )
        if not result.is_success:
            return await _send_error(interaction, result.message)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description=result.message,
            delete_after=60,
        )
        self.logger.info("Stopped playback in guild %s", guild.id)

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    @handle_errors()
    async def skip(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        result = await self.music_api.skip_track(
            guild.id,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        if not result.is_success or not result.data:
            return await _send_error(interaction, result.message)

        skipped_track, next_track = (
            result.data.get("before", None),
            result.data.get("after", None),
        )
        embed = discord.Embed(
            title="Трек пропущен" if skipped_track else "Очередь пуста",
            color=config.Color.INFO,
        )
        if next_track:
            embed.add_field(
                name="Сейчас играет",
                value=f"[{next_track.title}]({next_track.uri})",
                inline=False,
            )
            if next_track.artworkUrl:
                embed.set_thumbnail(url=next_track.artworkUrl)

        if skipped_track:
            embed.add_field(
                name="Пропущенный трек",
                value=f"[{skipped_track.title}]({skipped_track.uri})",
                inline=False,
            )

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            embed=embed,
            delete_after=60,
        )
        self.logger.info("Skipped track in guild %s", guild.id)

    @app_commands.command(name="pause", description="Приостановить воспроизведение")
    @app_commands.guild_only()
    @handle_errors()
    async def pause(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        result = await self.music_api.pause_player(guild.id)
        if not result.is_success:
            return await _send_error(interaction, result.message)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description="Воспроизведение приостановлено.",
            delete_after=60,
        )
        self.logger.info("Paused playback in guild %s", guild.id)

    @app_commands.command(name="resume", description="Продолжить воспроизведение")
    @app_commands.guild_only()
    @handle_errors()
    async def resume(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        result = await self.music_api.resume_player(guild.id)
        if not result.is_success:
            return await _send_error(interaction, result.message)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description="Воспроизведение продолжено.",
            delete_after=60,
        )
        self.logger.info("Resumed playback in guild %s", guild.id)

    @app_commands.command(name="queue", description="Показать текущую очередь")
    @app_commands.guild_only()
    @handle_errors()
    async def queue(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = True,
    ) -> None:
        guild = await self._require_guild(interaction)

        result = await self.music_api.get_queue(guild.id)
        if result.status is MusicResultStatus.ERROR:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.ERROR,
                description=result.message,
                ephemeral=True,
            )
            return

        if not result.data:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                title="Очередь пуста",
                description="В очереди нет треков.",
                ephemeral=True,
            )
            return

        paginator = QueuePaginator(interaction.user.id, result.data)
        await paginator.send(interaction, ephemeral=ephemeral)
        self.logger.debug("Sent queue paginator for guild %s", guild.id)

    @app_commands.command(
        name="volume", description="Установить громкость воспроизведения (0-200)"
    )
    @app_commands.describe(value="Громкость от 0 до 200")
    @app_commands.guild_only()
    @handle_errors()
    async def volume(
        self,
        interaction: Interaction,
        value: app_commands.Range[int, 0, 200] | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if value is None:
            volume = await self.music_api.get_volume(guild.id)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=f"Текущая громкость {volume}%.",
                delete_after=30,
            )
            return
        if not 0 <= value <= 1000:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Громкость должна быть от 0 до 200.",
            )
            return

        result = await self.music_api.set_volume(guild.id, value)
        if not result.is_success:
            return await _send_error(interaction, result.message)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=f"Громкость установлена на {value}%.",
            delete_after=60,
        )

    @app_commands.command(
        name="leave", description="Отключить бота от голосового канала"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def leave(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        result = await self.music_api.leave(guild)

        if result.status is MusicResultStatus.ERROR:
            return await _send_error(interaction, result.message)
        if result.status is MusicResultStatus.FAILURE:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=result.message,
                ephemeral=True,
            )
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.INFO,
            description="Отключился от голосового канала.",
            title="До свидания ",
            delete_after=60,
        )
        self.logger.info("Left voice channel in guild %s", guild.id)

    @app_commands.command(
        name="rotate", description="Переместить текущий трек в конец очереди"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def rotate(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        result = await self.music_api.rotate_current_track(
            guild.id,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        if result.status is MusicResultStatus.ERROR:
            return await _send_error(interaction, result.message)
        if result.status is MusicResultStatus.FAILURE:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=result.message,
                ephemeral=True,
            )
            return
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description="Трек перемещён в конец очереди.",
            delete_after=60,
        )
        self.logger.info("Rotated current track in guild %s", guild.id)

    @app_commands.command(name="shuffle", description="Перемешать очередь")
    @app_commands.guild_only()
    @handle_errors()
    async def shuffle(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        result = await self.music_api.shuffle_queue(
            guild.id,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )
        if not result.is_success:
            return await _send_error(interaction, result.message)

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description="Очередь перемешана.",
            delete_after=60,
        )

    @app_commands.command(name="repeat", description="Включить/выключить повтор.")
    @app_commands.describe(mode="off — выкл, queue — повтор очереди")
    @app_commands.guild_only()
    @handle_errors()
    async def repeat(
        self,
        interaction: Interaction,
        mode: RepeatMode | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        result = await self.music_api.set_repeat(
            guild.id,
            mode,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        data = result.data
        if not result.is_success or not data:
            return await _send_error(interaction, result.message)

        new_mode = data.get("mode")

        msg = (
            "Повтор **отключён**"
            if new_mode == RepeatMode.OFF
            else "Повтор очереди **включён**"
        )
        color = (
            config.Color.WARNING if new_mode == RepeatMode.OFF else config.Color.SUCCESS
        )

        embed = discord.Embed(
            title="Залупливание",
            description=msg,
            color=color,
        )
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)


class QueuePaginator(discord.ui.View):
    def __init__(
        self,
        author_id: int,
        player: Player,
        *,
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.author_id: Final = author_id
        self.player: Final = player
        self.page_size: Final = config.PAGE_SIZE
        self.page = 0

        sec_button = discord.ButtonStyle.secondary
        prim_button = discord.ButtonStyle.primary
        dan_button = discord.ButtonStyle.danger

        self.first_btn = discord.ui.Button[Self](label="⏮", style=sec_button, row=0)
        self.prev_btn = discord.ui.Button[Self](label="◀", style=sec_button, row=0)
        self.next_btn = discord.ui.Button[Self](label="▶", style=sec_button, row=0)
        self.last_btn = discord.ui.Button[Self](label="⏭", style=sec_button, row=0)
        self.update_btn = discord.ui.Button[Self](label="⭮", style=prim_button, row=1)
        self.close_btn = discord.ui.Button[Self](label="✕", style=dan_button, row=1)

        self.first_btn.callback = self.first
        self.prev_btn.callback = self.prev
        self.next_btn.callback = self.next
        self.last_btn.callback = self.last
        self.update_btn.callback = self.update
        self.close_btn.callback = self.close

        self.add_item(self.first_btn)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.last_btn)
        self.add_item(self.update_btn)
        self.add_item(self.close_btn)
        self._update_buttons()

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Попрошу не трогать", ephemeral=True
            )
            return False
        return True

    def _pages_count(self) -> int:
        total = max(len(self.player.queue) - 1, 0)
        return max((total + self.page_size - 1) // self.page_size, 1)

    def _update_buttons(self) -> None:
        pages = self._pages_count()
        self.first_btn.disabled = self.page == 0 or pages == 1
        self.prev_btn.disabled = self.page == 0 or pages == 1
        self.next_btn.disabled = self.page >= pages - 1 or pages == 1
        self.last_btn.disabled = self.page >= pages - 1 or pages == 1

    def _make_embed(self) -> discord.Embed:
        q = self.player.queue
        embed = discord.Embed(title="Очередь воспроизведения", color=config.Color.INFO)

        if q:
            now = q[0]
            embed.add_field(
                name="Сейчас играет",
                value=f"[{now.title}]({now.uri})",
                inline=False,
            )

        start = 1 + self.page * self.page_size
        end = min(len(q), start + self.page_size)

        if start < len(q):
            lines = [
                f"{idx}. [{track.title}]({track.uri})"
                for idx, track in enumerate(q[start:end], start=start)
            ]
            if lines:
                embed.add_field(
                    name="Далее",
                    value="\n".join(lines),
                    inline=False,
                )

        # Using getattr to access private/protected member safely in python
        mode = getattr(self.player, "_queue_repeat", False)
        embed.set_footer(
            text=(
                f"Стр. {self.page + 1}/{self._pages_count()}"
                f" • Всего: {len(q)}"
                f" • Повтор: {'вкл.' if mode else 'выкл.'}"
            )
        )
        return embed

    async def send(self, interaction: Interaction, *, ephemeral: bool) -> None:
        await interaction.response.send_message(
            embed=self._make_embed(),
            view=self,
            ephemeral=ephemeral,
            silent=True,
        )

    async def _update_view(self, interaction: Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    async def first(self, interaction: Interaction) -> None:
        self.page = 0
        await self._update_view(interaction)

    async def prev(self, interaction: Interaction) -> None:
        self.page = max(self.page - 1, 0)
        await self._update_view(interaction)

    async def next(self, interaction: Interaction) -> None:
        self.page = min(self.page + 1, self._pages_count() - 1)
        await self._update_view(interaction)

    async def last(self, interaction: Interaction) -> None:
        self.page = self._pages_count() - 1
        await self._update_view(interaction)

    async def update(self, interaction: Interaction) -> None:
        self.page = 0
        await self._update_view(interaction)

    async def close(self, interaction: Interaction) -> None:
        self.first_btn.disabled = True
        self.prev_btn.disabled = True
        self.next_btn.disabled = True
        self.last_btn.disabled = True
        self.update_btn.disabled = True
        self.close_btn.disabled = True
        await interaction.response.edit_message(view=None)
        self.stop()

    @override
    async def on_timeout(self) -> None:
        self.first_btn.disabled = True
        self.prev_btn.disabled = True
        self.next_btn.disabled = True
        self.last_btn.disabled = True
        self.update_btn.disabled = True
        self.close_btn.disabled = True
        self.stop()


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(MusicCog(bot))
