"""Music Cog Controller."""

import logging
from collections.abc import Sequence
from itertools import groupby

import discord
from discord import Interaction, Member, app_commands
from discord.ext import commands, tasks

import config
from api.music import (
    MusicResultStatus,
    MusicService,
    MusicSession,
    QueueSnapshot,
    RepeatMode,
    TrackGroup,
    TrackInfo,
    VoiceCheckResult,
)
from framework import BaseCog, FeedbackUI, handle_errors
from utils import truncate_text

from .ui import (
    format_duration,
    send_error,
    send_info,
    send_success,
    send_warning,
)
from .views import (
    QueuePaginationAdapter,
    QueuePaginator,
    SessionSummaryView,
    TrackControllerManager,
)

LOGGER = logging.getLogger("MusicCog")


def _format_voice_result_message(
    result: VoiceCheckResult,
    to_channel: discord.abc.GuildChannel | None,
    from_channel: discord.abc.GuildChannel | None,
) -> str:
    messages = {
        VoiceCheckResult.ALREADY_CONNECTED: "Уже подключён к {0}",
        VoiceCheckResult.CHANNEL_EMPTY: "Голосовой канал {0} пуст!",
        VoiceCheckResult.CONNECTION_FAILED: "Ошибка подключения к {0}",
        VoiceCheckResult.INVALID_CHANNEL_TYPE: "Неверный тип голосового канала",
        VoiceCheckResult.MOVED_CHANNELS: "Переместился {1} -> {0}",
        VoiceCheckResult.SUCCESS: "Успешно подключился к {0}",
        VoiceCheckResult.USER_NOT_IN_VOICE: "Вы должны быть в голосовом канале!",
        VoiceCheckResult.USER_NOT_MEMBER: "Неверный тип пользователя",
    }
    msg = messages.get(result, "Неизвестная ошибка")
    fm1 = to_channel.mention if to_channel else "Неизвестный канал"
    fm2 = from_channel.mention if from_channel else "Неизвестный канал"

    return msg.format(fm1, fm2)


class MusicCog(BaseCog):
    """Music playback controller."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.service = MusicService(bot)
        self.track_controller_manager = TrackControllerManager(bot)
        self.service.controller_manager = self.track_controller_manager

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.service.initialize()
        self.auto_leave_monitor.start()

    async def cog_unload(self) -> None:
        if self.auto_leave_monitor.is_running():
            self.auto_leave_monitor.cancel()
        await self.service.cleanup()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.service.initialize()

    @commands.Cog.listener()
    async def on_music_session_end(
        self, guild_id: int, session: MusicSession, channel_id: int
    ) -> None:
        """Handle music session end event."""
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        if not session.tracks:
            return

        embed = self._create_session_summary_embed(session)
        view = SessionSummaryView(session=session, timeout=300.0)

        try:
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
        except Exception:
            LOGGER.exception("Failed to send session summary to channel %s", channel_id)

    def _create_session_summary_embed(self, session: MusicSession) -> discord.Embed:
        """Create session summary embed."""
        embed = discord.Embed(
            title="Сессия закончена",
            color=config.Color.INFO,
            timestamp=session.start_time,
        )

        stats_text = self._format_session_stats(session)
        embed.add_field(name="В общем:", value=stats_text, inline=True)

        tracks_text = self._format_recent_tracks(session.tracks, max_tracks=30)
        embed.add_field(name="Недавние треки:", value=tracks_text, inline=False)

        return embed

    def _format_session_stats(self, session: MusicSession) -> str:
        """Format session statistics."""
        total_tracks = len(session.tracks)
        skipped_tracks = sum(1 for t in session.tracks if t.skipped)

        stats_parts = [f"**Всего:** {total_tracks} шт."]

        if skipped_tracks:
            stats_parts.append(f"(скипов: {skipped_tracks})")

        stats_parts.append(f"\n**Заказчиков:** {len(session.participants)} чел.")

        return "".join(stats_parts)

    def _group_consecutive_tracks(
        self, tracks: Sequence[TrackInfo]
    ) -> list[TrackGroup]:
        """Group consecutive tracks with the same parameters.

        Tracks are considered the same if they have the same: title, uri, skipped.
        """

        def key(t: TrackInfo):
            return (t.title, t.uri, t.skipped)

        groups: list[TrackGroup] = []
        for (title, uri, skipped), group in groupby(tracks, key):
            count = sum(1 for _ in group)
            groups.append(
                TrackGroup(title=title, uri=uri, skipped=skipped, count=count)
            )
        return groups

    def _format_track_group(self, group: TrackGroup) -> str:
        """Format one group of tracks for display."""
        status_marker = "~~" if group.skipped else ""
        count_str = f" **×{group.count}**" if group.count > 1 else ""
        track_str = (
            f"[{truncate_text(group.title, 45, placeholder='...')}]({group.uri})"
        )
        return f"{status_marker}{track_str}{count_str}{status_marker}"

    def _format_recent_tracks(
        self, tracks: Sequence[TrackInfo], max_tracks: int = 30
    ) -> str:
        """Format a list of recent tracks with grouping.

        Args:
            tracks: List of tracks from the session
            max_tracks: Maximum number of tracks to display (before grouping)

        Returns:
            Formatted text for embed field (truncated)

        """
        recent_tracks = tracks[-max_tracks:]
        grouped = self._group_consecutive_tracks(recent_tracks)
        formatted_groups = [self._format_track_group(group) for group in grouped]
        result = "\n".join(reversed(formatted_groups)) + "\n"
        if len(result) >= config.MAX_EMBED_FIELD_LENGTH:
            result = result[:1018].rsplit("\n", 1)[0] + "\n..."
        return result or "*(пусто)*"

    @tasks.loop(seconds=config.MUSIC_AUTO_LEAVE_CHECK_INTERVAL)
    async def auto_leave_monitor(self) -> None:
        await self.service.check_auto_leave()

    @auto_leave_monitor.before_loop
    async def before_auto_leave_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="join", description="Подключиться к голосовому каналу")
    @app_commands.guild_only()
    @handle_errors()
    async def join(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        if not isinstance(interaction.user, Member):
            return await send_warning(interaction, "Вы не участник сервера.")

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await send_warning(
                interaction, "Вы должны быть в голосовом канале!", ephemeral=True
            )

        channel = interaction.user.voice.channel
        check_result, from_channel = await self.service.join(guild, channel)

        msg = _format_voice_result_message(check_result, channel, from_channel)

        if check_result.status is MusicResultStatus.ERROR:
            await send_error(interaction, msg)
        elif check_result.status is MusicResultStatus.FAILURE:
            await send_warning(interaction, msg, ephemeral=True)
        else:
            await send_info(interaction, msg, delete_after=60)

    @app_commands.command(
        name="play", description="Воспроизведение музыки с YT, SoundCloud, Y.Music и VK"
    )
    @app_commands.describe(query="URL или название")
    @app_commands.guild_only()
    @handle_errors()
    async def play(self, interaction: Interaction, query: str) -> None:
        guild = await self._require_guild(interaction)
        if (
            not isinstance(interaction.user, Member)
            or not interaction.user.voice
            or not interaction.user.voice.channel
        ):
            return await send_warning(
                interaction, "Зайдите в голосовой канал!", ephemeral=True
            )

        await interaction.response.defer()

        channel = interaction.user.voice.channel

        result = await self.service.play(
            guild,
            channel,
            query,
            interaction.user.id,
            interaction.channel_id,
        )

        if not result.is_success or isinstance(result.data, tuple):
            if isinstance(result.data, tuple):
                check, from_ch = result.data
                await send_warning(
                    interaction, _format_voice_result_message(check, channel, from_ch)
                )
            else:
                await send_error(interaction, result.message)
            return

        data = result.data
        duration_ms = await self.service.get_queue_duration(guild.id)

        if not data:
            await send_warning(
                interaction, "Ничего не нашлось. Попробуйте ещё раз.", ephemeral=True
            )
            return
        delay_sec = (duration_ms / 1000) + 60

        if data["type"] == "track":
            track = data["track"]
            embed = discord.Embed(
                title="Сейчас играет" if not data["playing"] else "Добавлено в очередь",
                description=f"[{track.title}]({track.uri})",
                color=config.Color.INFO,
            )
            if track.artwork_url:
                embed.set_thumbnail(url=track.artwork_url)
            embed.add_field(name="Длительность", value=format_duration(track.length))
            embed.set_footer(
                text=f"Запросил: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )

            await FeedbackUI.send(
                interaction, embed=embed, delete_after=min(delay_sec, 600)
            )

        elif data["type"] == "playlist":
            playlist = data["playlist"]
            embed = discord.Embed(
                title=f"Добавлен плейлист **{playlist.name}**",
                description=f"Треков: {len(playlist.tracks)}",
                color=config.Color.INFO,
            )
            duration = sum(t.length for t in playlist.tracks)
            embed.add_field(name="Длительность", value=format_duration(duration))
            if playlist.tracks:
                embed.set_thumbnail(url=playlist.tracks[0].artwork_url or "")
            embed.set_footer(
                text=f"Запросил: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )

            await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(
        name="stop", description="Остановить воспроизведение и очистить очередь"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def stop(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.stop(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.is_success:
            await send_info(interaction, "Остановлено")
        else:
            await send_warning(interaction, "Нет проигрывателя")

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    @handle_errors()
    async def skip(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.skip(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.status is MusicResultStatus.FAILURE:
            await send_warning(interaction, "Нет проигрывателя")
            return

        if not res.is_success or not res.data:
            return await send_error(interaction, res.message)

        skipped = res.data["before"]
        next_track = res.data["after"]

        if not skipped and not next_track:
            await send_warning(interaction, "Нечего пропускать", ephemeral=True)
            return

        embed = discord.Embed(
            title="Трек пропущен",
            description=f"[{skipped.title}]({skipped.uri})" if skipped else "???",
            color=config.Color.INFO,
        )
        if next_track:
            embed.add_field(
                name="Далее",
                value=f"[{next_track.title}]({next_track.uri})",
                inline=False,
            )
        embed.set_thumbnail(url=skipped.artwork_url if skipped else None)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(name="queue", description="Очередь")
    @app_commands.describe(ephemeral="Скрыть сообщение")
    @app_commands.guild_only()
    @handle_errors()
    async def queue(self, interaction: Interaction, ephemeral: bool = True) -> None:
        guild = await self._require_guild(interaction)

        async def fetch() -> QueueSnapshot | None:
            res = await self.service.get_queue(guild.id)
            return res.data

        data = await fetch()
        if not data:
            await send_warning(interaction, "Очередь пуста", ephemeral=True)
            return

        adapter = QueuePaginationAdapter(data)
        view = QueuePaginator(adapter, fetch, interaction.user.id)
        await view.prepare()
        await view.send(interaction, ephemeral=ephemeral)

    @app_commands.command(name="volume", description="Установить громкость (0-200)")
    @app_commands.describe(value="Оставьте пустым, чтобы узнать громкость")
    @app_commands.guild_only()
    @handle_errors()
    async def volume(
        self,
        interaction: Interaction,
        value: app_commands.Range[int, 0, 200] | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if value is None:
            vol = await self.service.get_volume(guild.id)
            return await send_info(interaction, f"Громкость: {vol}%")

        res = await self.service.set_volume(guild.id, value)
        if res.is_success:
            await send_success(interaction, f"Громкость: {res.data}%")
        else:
            await send_error(interaction, res.message)

    @app_commands.command(name="leave", description="Выйти")
    @app_commands.guild_only()
    @handle_errors()
    async def leave(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.leave(guild)
        match res.status:
            case MusicResultStatus.SUCCESS:
                await send_info(interaction, "Отключился", title="До свидания ❤️")
            case MusicResultStatus.FAILURE:
                await send_warning(interaction, "Нет проигрывателя")
            case MusicResultStatus.ERROR:
                await send_error(interaction, res.message)

    @app_commands.command(name="shuffle", description="Перемешать")
    @app_commands.guild_only()
    @handle_errors()
    async def shuffle(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.shuffle(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.is_success:
            await send_success(interaction, "Перемешано")
        else:
            await send_warning(interaction, "Нет проигрывателя")

    @app_commands.command(
        name="rotate", description="Переместить тек. трек в конец очереди"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def rotate(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.rotate(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if not res.is_success:
            await send_warning(interaction, "Нет проигрывателя")
            return
        if not res.data or not res.data["skipped"]:
            await send_warning(interaction, "Нечего перемещать", ephemeral=True)
            return
        moved_track = res.data["skipped"]
        next_track = res.data["next"]

        embed = discord.Embed(
            title="Трек перемещён в конец",
            description=f"[{moved_track.title}]({moved_track.uri})",
            color=config.Color.INFO,
        )
        embed.add_field(
            name="Далее",
            value=f"[{next_track.title}]({next_track.uri})"
            if next_track
            else "*Тот же самый трек*",
            inline=False,
        )
        embed.set_thumbnail(url=moved_track.artwork_url)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(name="repeat", description="Включить/выключить повтор.")
    @app_commands.describe(
        mode="off — выкл, queue — повтор очереди, track - повтор трека"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def repeat(
        self,
        interaction: Interaction,
        mode: RepeatMode | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        result = await self.service.set_repeat(
            guild.id,
            mode,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        data = result.data
        if not result.is_success or not data:
            return await send_warning(interaction, "Нет проигрывателя")

        new_mode = data.get("mode")

        msg = (
            "Повтор **отключён**"
            if new_mode is RepeatMode.OFF
            else "Повтор очереди **включён**"
            if new_mode is RepeatMode.QUEUE
            else "Повтор трека **включён**"
        )
        color = (
            config.Color.WARNING if new_mode is RepeatMode.OFF else config.Color.SUCCESS
        )

        embed = discord.Embed(
            title="Залупливание",
            description=msg,
            color=color,
        )
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(name="pause", description="Пауза")
    @app_commands.guild_only()
    @handle_errors()
    async def pause(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.pause(guild.id)
        if res.is_success:
            await send_info(interaction, "Воспроизведение приостановлено")
        else:
            await send_warning(interaction, "Нет проигрывателя")

    @app_commands.command(name="resume", description="Продолжить")
    @app_commands.guild_only()
    @handle_errors()
    async def resume(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.resume(guild.id)
        if res.is_success:
            await send_info(interaction, "Воспроизведение продолжено")
        else:
            await send_warning(interaction, "Нет проигрывателя")

    # @commands.command(name="test_4006")
    # @commands.is_owner()
    # async def test_4006(self, ctx: commands.Context) -> None:
    #     player = self.service.get_player(ctx.guild.id)
    #     if not player:
    #         await ctx.reply("No player to test on.")
    #         return

    #     fake_payload = {
    #         "code": 4006,
    #         "reason": "Session is no longer valid.",
    #         "byRemote": True,
    #     }
    #     event = mafic.WebSocketClosedEvent(payload=fake_payload, player=player)
    #     await self.service._on_websocket_closed(event)
    #     await ctx.reply("Simulated 4006 close; healer/cleanup should have run.")


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(MusicCog(bot))
