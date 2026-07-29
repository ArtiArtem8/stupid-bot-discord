"""Music Cog Controller."""

import logging
from typing import override

import discord
from discord import Interaction, Member, app_commands
from discord.ext import commands, tasks

import config
from api.music import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    MusicResultStatus,
    MusicSession,
    QueueSnapshot,
    RepeatMode,
    VoiceCheckResult,
    VoiceJoinResult,
)
from api.music.healer import SessionHealer
from api.music.models import (
    MusicResult,
    PlayResponseData,
    QueuePlacement,
    TrackExceptionPayload,
)
from api.music.protocols import ControllerManagerProtocol, HealerProtocol
from api.music.service import (
    ConnectionManager,
    CoreMusicService,
    MusicEventHandlers,
    StateManager,
    UIOrchestrator,
)
from di.container import Container
from framework import BaseCog, FeedbackUI, handle_errors
from repositories.volume_repository import VolumeRepository

from .feedback import (
    send_error,
    send_info,
    send_success,
    send_warning,
    send_warning_no_player,
)
from .presentation import (
    build_playlist_added_embed,
    build_repeat_embed,
    build_rotate_embed,
    build_session_summary_embed,
    build_skip_embed,
    build_track_added_embed,
    build_track_exception_embed,
)
from .responder import MusicInteractionResponder
from .views import (
    QueuePaginationAdapter,
    QueuePaginator,
    QueueUndoView,
    SessionSummaryView,
    TrackControllerManager,
)

logger = logging.getLogger(__name__)


def _format_voice_result_message(
    result: VoiceCheckResult,
    to_channel: discord.abc.GuildChannel | None,
    from_channel: discord.abc.GuildChannel | None,
) -> str:
    messages = {
        VoiceCheckResult.ALREADY_CONNECTED: "Уже подключён к {0}",
        VoiceCheckResult.CHANNEL_EMPTY: "Голосовой канал {0} пуст!",
        VoiceCheckResult.CONNECTION_FAILED: "Ошибка подключения к {0}",
        VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE: MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
        VoiceCheckResult.TIMEOUT: "Время подключения к {0} **истекло**"
        + "\n*Попробуйте сменить регион этого канала!*",
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

        # Dependency Injection Setup
        self.container = Container()
        self.container.register(commands.Bot, factory=lambda _c: bot)
        self.container.register(ConnectionManager)
        self.container.register(StateManager)
        self.container.register(VolumeRepository)
        self.container.register(
            ControllerManagerProtocol, implementation=TrackControllerManager
        )
        self.container.register(UIOrchestrator)

        self.container.register(HealerProtocol, implementation=SessionHealer)

        self.container.register(MusicEventHandlers)
        self.container.register(CoreMusicService)

        self.service = self.container.resolve(CoreMusicService)

    @override
    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.service.initialize()
        self.auto_leave_monitor.start()

    @override
    async def cog_unload(self) -> None:
        if self.auto_leave_monitor.is_running():
            self.auto_leave_monitor.cancel()
        await self.service.cleanup()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.service.initialize()

    @commands.Cog.listener()
    async def on_music_session_end(
        self, _guild_id: int, session: MusicSession, channel_id: int
    ) -> None:
        """Handle music session end event."""
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        if not session.tracks:
            return

        embed = build_session_summary_embed(session)
        view = SessionSummaryView(session=session, timeout=300.0)

        try:
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
        except Exception:
            logger.exception("Failed to send session summary to channel %s", channel_id)

    @commands.Cog.listener()
    async def on_music_track_exception(self, payload: TrackExceptionPayload) -> None:
        """Handle dispatched track exception payloads."""
        channel_id = payload.channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        embed = build_track_exception_embed(payload)

        try:
            await channel.send(embed=embed, delete_after=60)
        except Exception:
            logger.exception("Failed to send track exception message to %s", channel_id)

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

        channel = await self._get_voice_channel_for_play(interaction)
        if not channel:
            return

        check_result, from_channel = await self._join_for_join_command(
            interaction, guild, channel
        )
        await self._send_join_feedback(
            interaction,
            result=check_result,
            channel=channel,
            from_channel=from_channel,
            delete_after=60,
            warn_on_failure=True,
        )

    @app_commands.command(
        name="play", description="Воспроизведение музыки с YT, SoundCloud, Y.Music и VK"
    )
    @app_commands.describe(query="URL или название")
    @app_commands.guild_only()
    @handle_errors()
    async def play(self, interaction: Interaction, query: str) -> None:
        await self._run_play_command(interaction, query, "end")

    @app_commands.command(
        name="play-next",
        description="Добавить трек или плейлист в начало очереди",
    )
    @app_commands.describe(query="URL или название")
    @app_commands.guild_only()
    @handle_errors()
    async def play_next(self, interaction: Interaction, query: str) -> None:
        await self._run_play_command(interaction, query, "next")

    async def _run_play_command(
        self,
        interaction: Interaction,
        query: str,
        placement: QueuePlacement,
    ) -> None:
        guild = await self._require_guild(interaction)
        responder = MusicInteractionResponder(interaction)
        if not query.strip():
            await responder.send_private_failure("Укажите название или ссылку на трек.")
            return

        channel = await self._get_voice_channel_for_play(interaction)
        if not channel:
            return

        result = await responder.await_with_defer_budget(
            self.service.play(
                guild,
                channel,
                query.strip(),
                interaction.user.id,
                interaction.channel_id,
                placement=placement,
            )
        )

        data = await self._resolve_play_response_data(interaction, result, channel)
        if not data:
            return

        duration_ms = await self.service.get_queue_duration(guild.id)
        delay_sec = (duration_ms / 1000) + 60

        await self._send_play_feedback(interaction, data, delay_sec)

    async def _get_voice_channel_for_play(
        self, interaction: Interaction
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        if (
            not isinstance(interaction.user, Member)
            or not interaction.user.voice
            or not interaction.user.voice.channel
        ):
            await MusicInteractionResponder(interaction).send_private_failure(
                "Вы должны быть в голосовом канале!"
            )
            return None
        return interaction.user.voice.channel

    async def _join_for_join_command(
        self,
        interaction: Interaction,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> tuple[VoiceCheckResult, discord.abc.GuildChannel | None]:
        """Join on behalf of `/join`; `/play` delegates connection to the service."""
        return await MusicInteractionResponder(interaction).await_with_defer_budget(
            self.service.join(guild, channel)
        )

    async def _resolve_play_response_data(
        self,
        interaction: Interaction,
        result: MusicResult[PlayResponseData | VoiceJoinResult],
        channel: discord.abc.GuildChannel,
    ) -> PlayResponseData | None:
        data = result.data
        if isinstance(data, tuple):
            check, from_channel = data
            await self._handle_join_for_play(interaction, check, channel, from_channel)
            return None
        if result.status is MusicResultStatus.ERROR:
            await send_error(interaction, result.message)
            return None
        if (
            result.status is MusicResultStatus.FAILURE
            and result.message != "Nothing found"
        ):
            await send_error(interaction, result.message)
            return None
        if not data:
            await send_info(interaction, "Ничего не нашлось. Попробуйте ещё раз.")
            return None
        return data

    async def _handle_join_for_play(
        self,
        interaction: Interaction,
        result: VoiceCheckResult,
        channel: discord.abc.GuildChannel,
        from_channel: discord.abc.GuildChannel | None,
    ) -> bool:
        if result.status is MusicResultStatus.SUCCESS:
            return True
        await self._send_join_feedback(
            interaction,
            result=result,
            channel=channel,
            from_channel=from_channel,
            delete_after=60,
        )
        return False

    async def _send_join_feedback(
        self,
        interaction: Interaction,
        result: VoiceCheckResult,
        channel: discord.abc.GuildChannel,
        from_channel: discord.abc.GuildChannel | None,
        *,
        delete_after: float | None = None,
        warn_on_failure: bool = False,
    ) -> None:
        msg = _format_voice_result_message(result, channel, from_channel)
        if result.status is MusicResultStatus.ERROR:
            await send_error(interaction, msg)
            return
        if result.status is MusicResultStatus.FAILURE:
            if warn_on_failure:
                await send_warning(
                    interaction, msg, ephemeral=True, delete_after=delete_after
                )
            else:
                await send_info(interaction, msg, delete_after=delete_after)
            return
        await send_info(interaction, msg, delete_after=delete_after)

    async def _send_no_player_or_unavailable(
        self, interaction: Interaction, result: MusicResult[object]
    ) -> None:
        if result.message == MUSIC_SERVICE_UNAVAILABLE_MESSAGE:
            await send_warning(
                interaction,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
                ephemeral=True,
                delete_after=60,
            )
            return
        await send_warning_no_player(interaction)

    async def _send_play_feedback(
        self, interaction: Interaction, data: PlayResponseData, delay_sec: float
    ) -> None:
        match data["type"]:
            case "track":
                embed = build_track_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                )
                delete_after = min(delay_sec, 480)
            case "playlist":
                embed = build_playlist_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                )
                delete_after = min(delay_sec, 600)

        view = None
        if data["undo_entries"] and interaction.guild_id is not None:
            view = QueueUndoView(
                guild_id=interaction.guild_id,
                expected_entries=data["undo_entries"],
                requester_id=interaction.user.id,
                remove_callback=self.service.remove_queued_entries,
                timeout=delete_after,
            )
        await FeedbackUI.send(
            interaction,
            embed=embed,
            view=view,
            delete_after=delete_after,
        )

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
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    @handle_errors()
    async def skip(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.skip(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.status is MusicResultStatus.FAILURE:
            await self._send_no_player_or_unavailable(interaction, res)
            return

        if not res.is_success or not res.data:
            return await send_error(interaction, res.message)

        skipped = res.data["before"]
        next_track = res.data["after"]

        if not skipped and not next_track:
            await send_warning(interaction, "Нечего пропускать", ephemeral=True)
            return

        embed = build_skip_embed(skipped, next_track)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(name="queue", description="Очередь")
    @app_commands.describe(ephemeral="Скрыть сообщение")
    @app_commands.guild_only()
    @handle_errors()
    async def queue(self, interaction: Interaction, ephemeral: bool = True) -> None:
        guild = await self._require_guild(interaction)

        res = await self.service.get_queue(guild.id)
        data = res.data
        if not data:
            if res.message == MUSIC_SERVICE_UNAVAILABLE_MESSAGE:
                await send_warning(
                    interaction,
                    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
                    ephemeral=True,
                    delete_after=60,
                )
                return
            await send_warning(interaction, "Очередь пуста", ephemeral=True)
            return

        async def fetch() -> QueueSnapshot | None:
            res = await self.service.get_queue(guild.id)
            return res.data

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
        res = await MusicInteractionResponder(interaction).await_with_defer_budget(
            self.service.leave(guild)
        )
        match res.status:
            case MusicResultStatus.SUCCESS:
                await send_info(interaction, "Отключился", title="До свидания ❤️")
            case MusicResultStatus.FAILURE:
                await self._send_no_player_or_unavailable(interaction, res)
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
            await self._send_no_player_or_unavailable(interaction, res)

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
            await self._send_no_player_or_unavailable(interaction, res)
            return
        if not res.data or not res.data["skipped"]:
            await send_warning(interaction, "Нечего перемещать", ephemeral=True)
            return
        moved_track = res.data["skipped"]
        next_track = res.data["next"]

        embed = build_rotate_embed(moved_track, next_track)
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
            return await self._send_no_player_or_unavailable(interaction, result)

        embed = build_repeat_embed(data.get("mode"))
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
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(name="resume", description="Продолжить")
    @app_commands.guild_only()
    @handle_errors()
    async def resume(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        res = await self.service.resume(guild.id)
        if res.is_success:
            await send_info(interaction, "Воспроизведение продолжено")
        else:
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(
        name="reconnect", description="Переподключиться в случае ошибок"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def heal(self, interaction: Interaction) -> None:
        if interaction.guild_id is None:
            await send_warning(interaction, "сервер не найден", ephemeral=True)
            return

        player = self.service.get_player(interaction.guild_id)
        if not player:
            await send_warning(interaction, "нет проигрывателя", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        restored = await self.service.heal(interaction.guild_id)
        if restored:
            await send_warning(
                interaction,
                title="Восстановлен",
                message="Попытка переподключения сделана",
                ephemeral=True,
            )
            return

        await send_warning(
            interaction,
            title="Не восстановлено",
            message=(
                "Переподключение выполнено, но трек не удалось снова запустить. "
                "Скорее всего, источник временно отдал ошибку."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(MusicCog(bot))
