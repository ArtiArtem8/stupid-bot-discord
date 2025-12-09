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

import asyncio
import logging
import time
from datetime import timedelta
from math import ceil
from typing import (
    Awaitable,
    Callable,
    Self,
    TypedDict,
    override,
)

import discord
import mafic
from discord import Interaction, Member, app_commands
from discord.abc import Connectable
from discord.channel import VocalGuildChannel
from discord.ext import commands, tasks

import config
from api import (
    MusicAPI,
    MusicResultStatus,
    MusicSession,
    Playlist,
    QueueSnapshot,
    RepeatMode,
    Track,
    VoiceCheckResult,
    VoiceJoinResult,
)
from api.music import MusicPlayer
from framework import (
    BaseCog,
    BasePaginator,
    FeedbackType,
    FeedbackUI,
    PaginationData,
    handle_errors,
)
from framework.pagination import PRIMARY
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


class MusicCog(BaseCog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.music_api = MusicAPI(bot)
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}
        self.track_controller_manager = TrackControllerManager(bot)

    # @property
    # def node(self) -> Any | None:
    #     """Expose node for LavalinkVoiceClient (accessed via getattr)."""
    #     return self.music_api.node

    async def interaction_check(self, interaction: Interaction[discord.Client]) -> bool:
        if interaction.guild_id != 748606123065475134:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                title="Приносим извинения. Команда находится в разработке",
                description="Мы переходим на на другой клиент и перерабатываем всю функциональность.",
                ephemeral=True,
            )
            return False
        return await super().interaction_check(interaction)

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
        if not guild.voice_client or not isinstance(guild.voice_client, MusicPlayer):
            return
        self.logger.debug("Voice state update: %s -> %s", before, after)
        bot_channel: Connectable = guild.voice_client.channel
        self.logger.debug("Bot channel: %s", bot_channel)
        affected_channels: set[Connectable] = set()
        if before.channel == bot_channel:
            affected_channels.add(bot_channel)
        if after.channel == bot_channel:
            affected_channels.add(bot_channel)

        if before.channel == bot_channel == after.channel and (
            before.deaf != after.deaf or before.self_deaf != after.self_deaf
        ):
            affected_channels.add(bot_channel)

        for channel in affected_channels:
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                LOGGER.debug("Channel is not a voice channel: %s", channel)
                continue
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

    @commands.Cog.listener()
    async def on_track_start(self, event: mafic.TrackStartEvent[MusicPlayer]) -> None:
        player = event.player
        requester = player.get_requester(event.track)
        session = self.music_api.sessions[player.guild.id]
        main_channel_id = (
            max(session.channel_usage, key=lambda k: session.channel_usage[k])
            if session.channel_usage
            else None
        )
        if not main_channel_id:
            return
        channel = self.bot.get_channel(main_channel_id)
        if channel:
            await self.track_controller_manager.create_for_user(
                guild_id=player.guild.id,
                user_id=requester or 0,
                channel=channel,
                player=player,
            )

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
            if url := track.artwork_url:
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
        self, interaction: Interaction, playlist: Playlist, delete_after: float
    ) -> None:
        try:
            embed = discord.Embed(
                title=f"Добавлен плейлист **{playlist.name}**",
                description=(f"Треков: {len(playlist.tracks)} шт."),
                color=config.Color.INFO,
            )
            if playlist.tracks and (url := playlist.tracks[0].artwork_url):
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
            if url := next_track.artwork_url:
                embed.set_thumbnail(url=url)

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

        async def fetch_queue() -> QueueSnapshot | None:
            res = await self.music_api.get_queue(guild.id)
            return res.data if res.is_success else None

        initial_data = await fetch_queue()
        if not initial_data:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Очередь пуста.",
                ephemeral=True,
            )
            return

        adapter = QueuePaginationAdapter(initial_data)
        view = QueuePaginator(
            adapter=adapter, refresh_callback=fetch_queue, user_id=interaction.user.id
        )
        await view.send(interaction, ephemeral=ephemeral)
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


type QueueRefreshCallback = Callable[[], Awaitable[QueueSnapshot | None]]


class QueuePaginationAdapter(PaginationData):
    """Adapts music queue data for the paginator."""

    def __init__(self, snapshot: QueueSnapshot, page_size: int = 20) -> None:
        self.snapshot = snapshot
        self.page_size = page_size

    def update_snapshot(self, snapshot: QueueSnapshot) -> None:
        """Update internal data with fresh snapshot."""
        self.snapshot = snapshot

    @override
    async def get_page_count(self) -> int:
        upcoming_count = len(self.snapshot.queue)
        return max((upcoming_count + self.page_size - 1) // self.page_size, 1)

    @override
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
            if self.snapshot.repeat_mode.value == "off"
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

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="Попрошу не трогать, вы не диджей.",
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
        # Initialize base buttons (Row 0)
        super().__init__(adapter, user_id, show_first_last=True)

        self.adapter = adapter
        self.refresh_callback = refresh_callback

        # Add Refresh button on Row 1
        self.refresh_btn = discord.ui.Button[Self](label="⭮", style=PRIMARY, row=1)
        self.refresh_btn.callback = self.refresh
        self.add_item(self.refresh_btn)

    async def refresh(self, interaction: Interaction) -> None:
        """Fetch fresh data via callback and update view."""
        new_data = await self.refresh_callback()

        if new_data:
            self.adapter.update_snapshot(new_data)
            self.page = 0
            await self._update_view(interaction)
        else:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Не удалось обновить очередь",
                ephemeral=True,
            )
            self.stop()


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
        adapter = SessionPaginationAdapter(self.session)
        paginator = BasePaginator(
            data=adapter, user_id=interaction.user.id, show_first_last=False
        )
        await paginator.send(interaction, ephemeral=True, silent=True)

    @override
    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                LOGGER.debug("Failed to edit message view: %s", self.message.id)
                pass


class SessionPaginationAdapter(PaginationData):
    """Adapts music session history for the paginator."""

    def __init__(self, session: MusicSession, page_size: int = 15) -> None:
        self.session = session
        self.page_size = page_size

    @override
    async def get_page_count(self) -> int:
        return max(1, ceil(len(self.session.tracks) / self.page_size))

    @override
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
            track_str = f"[{truncate_text(track.title, width=35, placeholder='...')}]({track.uri})"
            requester_str = f"(<@{track.requester_id}>)" if track.requester_id else ""
            lines.append(f"{idx}. {status}{track_str}{status} {requester_str}")

        embed.description = "\n".join(lines) if lines else "Пусто"

        total_pages = max(1, ceil(len(self.session.tracks) / self.page_size))
        embed.set_footer(
            text=f"Стр. {page + 1}/{total_pages} • {len(self.session.tracks)} всего"
        )
        return embed

    @override
    async def on_unauthorized(self, interaction: Interaction) -> None:
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="Попрошу не трогать, вы не диджей.",
            ephemeral=True,
        )


class TrackControllerManager:
    """Manages per-user track controllers per guild.
    Each controller exists only while its track is the current track.
    """

    def __init__(self, bot):
        self.bot = bot
        # guild_id -> { user_id -> Controller }
        self.controllers: dict[int, dict[int, TrackControllerView]] = {}

    async def destroy_for_guild(self, guild_id: int):
        """Destroy all controllers for a guild (on stop/leave)."""
        if guild_id not in self.controllers:
            return
        for view in self.controllers[guild_id].values():
            await view.destroy()
        self.controllers[guild_id].clear()

    async def create_for_user(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable,
        player: MusicPlayer,
    ):
        """Create a controller for requester when track starts playing."""
        # Clean old controllers of this user if exist
        self.controllers.setdefault(guild_id, {})
        if user_id in self.controllers[guild_id]:
            await self.controllers[guild_id][user_id].destroy()

        view = TrackControllerView(
            user_id=user_id,
            player=player,
            manager=self,
            guild_id=guild_id,
        )
        msg = await channel.send(embed=view.make_embed(), view=view, silent=True)
        view.message = msg
        self.controllers[guild_id][user_id] = view
        view.start_updater()

    async def destroy_specific(self, guild_id: int, user_id: int):
        if guild_id not in self.controllers:
            return
        view = self.controllers[guild_id].pop(user_id, None)
        if view:
            await view.destroy()

    async def destroy_all_for_track_end(self, guild_id: int):
        await self.destroy_for_guild(guild_id)


class TrackControllerView(discord.ui.View):
    def __init__(
        self,
        *,
        user_id: int,
        player: MusicPlayer,
        manager: TrackControllerManager,
        guild_id: int,
    ):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.player = player
        self.manager = manager
        self.guild_id = guild_id
        self.message: discord.Message | None = None
        self._task: asyncio.Task | None = None

    # --- Utility ---
    def make_embed(self) -> discord.Embed:
        track = self.player.current
        if not track:
            return discord.Embed(title="Ничего не играет", color=0x5865F2)
        pos = self.player.position or 0
        length = track.length
        bar = self._make_bar(pos, length)
        e = discord.Embed(
            title=f"🎵 {track.title}",
            description=f"[{bar}]`{pos // 1000}s / {length // 1000}s`",
            color=0x5865F2,
        )
        if track.artwork_url:
            e.set_thumbnail(url=track.artwork_url)
        return e

    def _make_bar(self, pos: int, length: int, width: int = 19) -> str:
        ratio = pos / length if length > 0 else 0
        filled = int(ratio * width)
        return "▬" * filled + "🔘" + "▬" * (width - filled)

    async def destroy(self):
        if self._task:
            self._task.cancel()
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    def start_updater(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            while True:
                await asyncio.sleep(5)
                if not self.player.current:
                    await self.manager.destroy_specific(self.guild_id, self.user_id)
                    return
                await self._safe_update()
        except asyncio.CancelledError:
            return

    async def _safe_update(self):
        if self.message:
            try:
                await self.message.edit(embed=self.make_embed(), view=self)
            except discord.HTTPException:
                pass

    # --- Permission check ---
    async def _check_owner(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Этот контроллер не ваш.",
                ephemeral=True,
            )
            return False
        return True

    # --- Buttons ---
    @discord.ui.button(label="⏮ 0s", style=discord.ButtonStyle.primary)
    async def restart(self, interaction: Interaction, _: discord.ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        await self.player.seek(0)
        await interaction.response.defer()
        await self._safe_update()

    @discord.ui.button(label="-10s", style=discord.ButtonStyle.secondary)
    async def back10(self, interaction: Interaction, _: discord.ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        new = max((self.player.position or 0) - 10000, 0)
        await self.player.seek(new)
        await interaction.response.defer()
        await self._safe_update()

    @discord.ui.button(label="⏯", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: Interaction, _: discord.ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        if self.player.paused:
            await self.player.resume()
        else:
            await self.player.pause()
        await interaction.response.defer()
        await self._safe_update()

    @discord.ui.button(label="+10s", style=discord.ButtonStyle.secondary)
    async def forward10(self, interaction: Interaction, _: discord.ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        new = min((self.player.position or 0) + 10000, self.player.current.length)
        await self.player.seek(new)
        await interaction.response.defer()
        await self._safe_update()

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.danger)
    async def skip(self, interaction: Interaction, _: discord.ui.Button[Self]):
        if not await self._check_owner(interaction):
            return
        await self.player.skip()
        await interaction.response.defer()

        await self.manager.destroy_all_for_track_end(self.guild_id)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(MusicCog(bot))
