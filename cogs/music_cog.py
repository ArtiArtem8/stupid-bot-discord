# -*- coding: utf-8 -*-
"""Music playback with Lavalink integration.

Provides:
- Playing music from various sources
- Queue management
- Playback control (pause, resume, skip, stop)
- Volume control
- Voice channel management

Requirements:
    - Lavalink server running
    - Environment variables: LAVALINK_HOST, LAVALINK_PORT, LAVALINK_PASSWORD
"""

import asyncio
import functools
import logging
import os
import time
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Concatenate,
    Coroutine,
    Optional,
    ParamSpec,
    TypeVar,
    Union,
    cast,
)

import discord
import lavaplay  # type: ignore
import lavaplay.player  # type: ignore
from discord import (
    Interaction,
    Member,
    StageChannel,
    VoiceChannel,
    app_commands,
)
from discord.abc import Snowflake
from discord.ext import commands

from config import MUSIC_DEFAULT_VOLUME, MUSIC_VOLUME_FILE
from utils import BaseCog, FailureUI, get_json, save_json

# Load environment variables
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", 2333))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

logger = logging.getLogger("MusicCog")
T = TypeVar("T")
P = ParamSpec("P")
AsyncFunc = Callable[P, Awaitable[T]]
CogT = TypeVar("CogT", bound="MusicCog")
VocalGuildChannel = Union[StageChannel, VoiceChannel]
VoiceCheckData = Optional[
    VocalGuildChannel | tuple[VocalGuildChannel, VocalGuildChannel]
]
MusicCommand = Callable[Concatenate[CogT, Interaction, P], Coroutine[Any, Any, T]]


class VoiceCheckResult(Enum):
    ALREADY_CONNECTED = ("✅ Уже подключён к {0}", True)
    CHANNEL_EMPTY = ("❌ Голосовой канал {0} пуст!", False)
    CONNECTION_FAILED = ("❌ Ошибка подключения к {0}", False)
    INVALID_CHANNEL_TYPE = ("❌ Неверный тип голосового канала", False)
    MOVED_CHANNELS = ("✅ Переместился {0} -> {1}", True)
    SUCCESS = ("✅ Успешно подключился к {0}", True)
    USER_NOT_IN_VOICE = ("❌ Вы должны быть в голосовом канале!", False)
    USER_NOT_MEMBER = ("❌ Неверный тип пользователя", False)

    def __init__(self, msg: str, is_success: bool):
        self._msg = msg
        self._is_success = is_success

    @property
    def msg(self) -> str:
        return self._msg

    @property
    def is_success(self) -> bool:
        return self._is_success


def _format_voice_result_message(
    result: VoiceCheckResult,
    data: VoiceCheckData,
) -> str:
    """Helper to format the message based on the result and data."""
    try:
        match result:
            case (
                VoiceCheckResult.ALREADY_CONNECTED
                | VoiceCheckResult.CHANNEL_EMPTY
                | VoiceCheckResult.CONNECTION_FAILED
                | VoiceCheckResult.SUCCESS
            ):
                channel = cast(VocalGuildChannel, data)
                return result.msg.format(channel.mention)
            case VoiceCheckResult.MOVED_CHANNELS:
                from_channel, to_channel = cast(
                    tuple[VocalGuildChannel, VocalGuildChannel], data
                )
                return result.msg.format(from_channel.mention, to_channel.mention)
            case _:
                return result.msg
    except (TypeError, AttributeError, ValueError, IndexError) as e:
        logger.error(
            f"Error formatting voice res message for {result.name}: {e}. Data: {data}"
        )
        return result.msg


def handle_errors() -> Callable[
    [MusicCommand[CogT, P, T]], MusicCommand[CogT, P, Optional[T]]
]:
    """Decorator to add error handling to asynchronous functions.

    This decorator wraps the provided function to catch and handle
    exceptions that may occur during its execution, specifically
    Discord-related exceptions and any other unexpected errors.
    Appropriate error messages are sent as responses to the Discord
    interaction, ensuring a graceful failure with user feedback.

    Returns:
        A decorated function with error handling logic.

    """

    def decorator(func: MusicCommand[CogT, P, T]) -> MusicCommand[CogT, P, Optional[T]]:
        @functools.wraps(func)
        async def wrapper(
            self: CogT,
            interaction: Interaction,
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> Optional[T]:
            """Wrapper that adds error handling."""
            try:
                return await func(self, interaction, *args, **kwargs)
            except discord.DiscordException as e:
                logger.exception(f"Discord error in {func.__name__}: {e!s}")
                await FailureUI.send_failure(
                    interaction,
                    title="Discord Ошибка",
                    description=f"❌ {type(e).__name__}: {e}",
                    ephemeral=True,
                )
            except Exception as e:
                logger.exception(f"Unexpected error in {func.__name__}: {e!s}")
                await FailureUI.send_failure(
                    interaction,
                    title="Внутренняя ошибка",
                    description=f"❌ {type(e).__name__}: {e}",
                    ephemeral=True,
                )
            return None

        return cast(MusicCommand[CogT, P, Optional[T]], wrapper)

    return decorator


class MusicCog(BaseCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.lavalink = lavaplay.Lavalink()
        self.node: lavaplay.Node | None = None

    async def cog_unload(self) -> None:
        if self.node is not None:
            await self.node.close()
        for node in self.lavalink.nodes:
            self.lavalink.destroy_node(node)

    async def cog_load(self):
        if self.bot.is_ready():
            await self.initialize_node()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.initialize_node()

    async def initialize_node(self):
        """Safely initialize Lavalink node."""
        if self.node and self.node.is_connect:
            return
        await self._connect_node()

    async def _get_player(self, guild_id: int) -> lavaplay.player.Player:
        """Get existing player or create new one with proper voice data."""
        await self.initialize_node()  # Ensure node is connected
        if self.node is None:
            self.node = self.lavalink.default_node
        player = self.node.get_player(guild_id)
        if not player:
            player = self.node.create_player(guild_id)
        return player

    async def _connect_node(self):
        """Full node connection sequence."""
        try:
            if self.lavalink.nodes:
                self.node = self.lavalink.default_node
            else:
                self.node = self.lavalink.create_node(
                    host=LAVALINK_HOST,
                    port=LAVALINK_PORT,
                    password=LAVALINK_PASSWORD,
                    user_id=self.bot.user.id if self.bot.user else 0,
                )
            self.node.set_event_loop(self.bot.loop)
            self.node.connect()
            await asyncio.wait_for(self._wait_for_connection(), timeout=10)
            logger.info("Node connected successfully")
        except Exception as e:
            logger.error("Node connection failed: %s", e)
            raise

    async def _check_and_reconnect_node(self) -> bool:
        """Verify Lavalink node connection."""
        if not self.node or not self.node.is_connect:
            logger.warning("Lavalink node not connected")
            try:
                await self._connect_node()
                return True
            except Exception as e:
                logger.error("Node reconnect failed: %s", e)
                return False
        return True

    async def _wait_for_connection(self):
        """Wait until node is fully connected."""
        while not self.node or not self.node.is_connect:
            await asyncio.sleep(0.1)

    async def _get_volume(self, guild_id: int) -> int:
        """Get volume for specific guild."""
        volume_data = get_json(MUSIC_VOLUME_FILE) or {}
        return volume_data.get(str(guild_id), MUSIC_DEFAULT_VOLUME)

    async def _set_volume(self, guild_id: int, volume: int):
        """Save volume for specific guild."""
        volume_data = get_json(MUSIC_VOLUME_FILE) or {}
        volume_data[str(guild_id)] = volume
        save_json(MUSIC_VOLUME_FILE, volume_data)

    async def send_response(
        self,
        interaction: Interaction,
        content: str,
        *,
        delete_after: float | None = None,
        ephemeral: bool = False,
        silent: bool = True,
        embed: discord.Embed | None = None,
    ) -> None:
        da = delete_after
        timer = f"\n-# Удалится <t:{int(time.time() + da)}:R>" if da else ""
        kwargs: dict[str, Any] = {
            "content": content + timer,
            "ephemeral": ephemeral,
            "embed": embed,
            "silent": silent,
        }
        if interaction.response.is_done():
            if delete_after:
                message = await interaction.followup.send(**kwargs, wait=True)
                await message.delete(delay=delete_after)
                return
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs, delete_after=delete_after)

    async def _get_player_or_handle_error(
        self, interaction: Interaction, *, needs_player: bool = True
    ) -> Optional[lavaplay.player.Player]:
        """Gets the Lavalink player for the interaction's guild.

        Handles node initialization, guild ID checks, and optionally player
        existence checks.Sends ephemeral error messages via interaction if checks fail.

        Args:
            interaction: The discord Interaction object.
            needs_player: If True, checks if the player exists and sends an error if not
                          If False, only checks for guild_id and node.

        Returns:
            The LavalinkPlayer if successful (and found, if needs_player=True),
            otherwise None.

        """
        if self.node is None:
            self.node = self.lavalink.default_node
            if not self.node.is_connect:
                logger.error("Lavalink node is unavailable.")
                error_msg = "Музыкальный сервис недоступен."
                await FailureUI.send_failure(
                    interaction,
                    title="Ошибка сервиса",
                    description=error_msg,
                    ephemeral=True,
                )
                return None

        if not interaction.guild_id:
            logger.error(
                f"Guild ID is None in command triggered by user {interaction.user.id}"
            )
            error_msg = "Не удалось определить ID сервера."
            try:
                await FailureUI.send_failure(
                    interaction,
                    title="Ошибка",
                    description=error_msg,
                    ephemeral=True,
                )
            except discord.HTTPException:
                logger.warning("Could not send guild_id error response.")
            return None

        player = await self._get_player(interaction.guild_id)

        if needs_player and not player:
            logger.debug(
                f"Player not found for guild {interaction.guild_id} in command "
                f"triggered by {interaction.user.id}"
            )
            error_msg = "Бот не играет музыку или не подключен к каналу."
            await FailureUI.send_failure(
                interaction,
                title="Ошибка",
                description=error_msg,
                ephemeral=True,
            )
            return None

        return player

    @app_commands.command(name="join", description="Присоединиться к голосовому каналу")
    @app_commands.guild_only()
    @handle_errors()
    async def join(self, interaction: Interaction):
        """Join your current voice channel."""
        await interaction.response.defer(ephemeral=True)
        result, data = await self._ensure_voice(interaction)
        message = _format_voice_result_message(result, data)
        log_msg = (
            f"Join command result for {interaction.user}: "
            f"{result.name}. Message: {message}"
        )
        logger.log(logging.INFO if result.is_success else logging.WARNING, log_msg)
        await interaction.followup.send(message, ephemeral=True, silent=True)

    @app_commands.command(
        name="play",
        description="Воспроизведение музыки с YT, SoundCloud "
        ", YaMusic и VK (ephemeral скрывает сообщение)",
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
    ):
        """Play a song from various supported platforms."""
        await interaction.response.defer(ephemeral=ephemeral)
        result, data = await self._ensure_voice(interaction)
        if not result.is_success:
            error_message = _format_voice_result_message(result, data)
            if result == VoiceCheckResult.USER_NOT_IN_VOICE:
                await self.send_response(
                    interaction,
                    "Зайдите в голосовой канал.",
                    delete_after=30,
                )
                return
            logger.warning(f"Play command failed for {interaction.user}: {result.name}")
            await FailureUI.send_failure(
                interaction,
                title="Ошибка",
                description="Код ошибки: " + error_message,
                delete_after=30,
            )
            return
        if not await self._check_and_reconnect_node():
            await FailureUI.send_failure(
                interaction,
                title="Ошибка сервиса",
                description="Музыкальный сервис недоступен. Попробуйте выйти и зайти.",
                delete_after=30,
            )
        guild_id = (await self._require_guild(interaction)).id
        player = await self._get_player(guild_id)
        volume = await self._get_volume(guild_id)
        await player.volume(volume)
        if self.node is None:
            self.node = self.lavalink.default_node
        try:
            tracks = await self.node.auto_search_tracks(query)
        except KeyError:
            await self.send_response(
                interaction,
                "💤 Трек не найден",
                delete_after=30,
            )
            return

        if isinstance(tracks, lavaplay.PlayList):
            logger.debug("Playlist found: %s tracks", len(tracks.tracks))
            await self._handle_playlist(interaction, player, tracks)
        elif isinstance(tracks, list) and len(tracks) > 0:
            logger.debug("Single track found: %s", tracks[0].title)
            await self._handle_track(interaction, player, tracks[0])
        else:
            logger.debug("No track results found for query: %s", query)
            await self.send_response(
                interaction,
                "💤 Результаты не найдены",
                delete_after=30,
            )

    async def _handle_track(
        self,
        interaction: Interaction,
        player: lavaplay.player.Player,
        track: lavaplay.Track,
    ):
        try:
            await player.play(track, requester=interaction.user.id)
            if len(player.queue) > 1:
                embed = discord.Embed(
                    title="✅ Трек добавлен в очередь",
                    description=f"[{track.title}]({track.uri})",
                    color=0xFFAE00,
                )
                embed.set_thumbnail(url=track.artworkUrl)
                return await interaction.followup.send(embed=embed, silent=True)
            embed = discord.Embed(
                title="🎵 Сейчас играет",
                description=f"[{track.title}]({track.uri})",
                color=0xFFAE00,
            )
            embed.set_thumbnail(url=track.artworkUrl)
            await interaction.followup.send(embed=embed, silent=True)
        except lavaplay.TrackLoadFailed as e:
            logger.error("Track load error: %s", e)
            await FailureUI.send_failure(
                interaction,
                title="Ошибка загрузки трека",
                description=f"Трек не был загружен из-за ошибки: {e.message}",
            )
        except Exception as e:
            logger.exception("Unexpected error in _handle_track: %s", e)
            await FailureUI.send_failure(
                interaction,
                title="Ошибка трека",
                description="Произошла ошибка во время воспроизведения трека",
            )

    async def _handle_playlist(
        self,
        interaction: Interaction,
        player: lavaplay.player.Player,
        playlist: lavaplay.PlayList,
    ):
        try:
            await player.play_playlist(playlist)
            await interaction.followup.send(
                f"🎶 Плейлист **{playlist.name}** с {len(playlist.tracks)} "
                "треками добавлен в очередь",
                silent=True,
            )
        except Exception as e:
            logger.exception("Unexpected error in _handle_playlist: %s", e)
            await FailureUI.send_failure(
                interaction,
                title="Ошибка плейлиста",
                description="Произошла ошибка во время воспроизведения плейлиста",
            )

    async def _ensure_voice(
        self, interaction: Interaction
    ) -> tuple[VoiceCheckResult, VoiceCheckData]:
        """Ensure bot is connected to voice channel."""
        member = interaction.user
        if not isinstance(member, Member):
            return VoiceCheckResult.USER_NOT_MEMBER, None
        if not member.voice:
            return VoiceCheckResult.USER_NOT_IN_VOICE, None
        voice_state = member.voice
        voice_channel = voice_state.channel
        if not isinstance(voice_channel, (VoiceChannel, StageChannel)):
            return VoiceCheckResult.INVALID_CHANNEL_TYPE, None

        guild = interaction.guild
        if not guild:
            logger.error("Guild context missing despite guild_only decorator.")
            return VoiceCheckResult.CONNECTION_FAILED, voice_channel
        voice_client = guild.voice_client

        if voice_client:
            if not voice_client or not isinstance(voice_client, LavalinkVoiceClient):
                logger.error("Voice client is not LavalinkVoiceClient")
                return VoiceCheckResult.CONNECTION_FAILED, voice_channel
            if voice_client.channel == voice_channel:
                try:
                    await voice_channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
                except discord.ClientException:
                    logger.debug("Voice client is already connected")
                return VoiceCheckResult.ALREADY_CONNECTED, voice_channel
            from_channel = voice_client.channel
            try:
                logger.info(
                    f"Moving from {from_channel.name} to "
                    f"{voice_channel.name} in guild {guild.id}"
                )
                await voice_client.move_to(voice_channel)
                return VoiceCheckResult.MOVED_CHANNELS, (from_channel, voice_channel)
            except Exception as e:
                logger.exception(
                    f"Failed to move voice client from {from_channel} to "
                    f"{voice_channel.name}: {e}"
                )
                return VoiceCheckResult.CONNECTION_FAILED, voice_channel
        try:
            if not any(m for m in voice_channel.members if not m.bot):
                return VoiceCheckResult.CHANNEL_EMPTY, voice_channel
            logger.info(f"Connecting to {voice_channel.name} in guild {guild.id}")
            if self.node is None:
                self.node = self.lavalink.default_node
            self.node.create_player(guild.id)
            await voice_channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
            return VoiceCheckResult.SUCCESS, voice_channel
        except discord.ClientException as e:
            logger.error(
                f"ClientException in {voice_channel.mention} connection: %s", e
            )
            return VoiceCheckResult.CONNECTION_FAILED, voice_channel
        except Exception as e:
            logger.exception(f"Voice connection error {voice_channel.mention}: %s", e)
            return VoiceCheckResult.CONNECTION_FAILED, voice_channel

    @app_commands.command(
        name="stop", description="Остановить воспроизведение и очистить очередь"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def stop(self, interaction: Interaction):
        """Stop the player and clear queue."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        await player.stop()  # clears queue
        await self.send_response(
            interaction,
            "Воспроизведение остановлено, очередь очищена",
            delete_after=15,
            silent=True,
        )
        logger.info(f"Player stopped and queue cleared in guild {interaction.guild_id}")

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    @handle_errors()
    async def skip(self, interaction: Interaction):
        """Skip to the next track in queue."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        if not player.queue:
            await interaction.followup.send("Нечего пропускать.", ephemeral=True)
            return
        await player.skip()
        await interaction.response.send_message(
            "⏭️ Текущий трек пропущен", delete_after=15, silent=True
        )
        logger.info(f"Track skipped for guild {interaction.guild_id}")

    @app_commands.command(
        name="pause", description="Поставить воспроизведение на паузу"
    )
    @app_commands.guild_only()
    @handle_errors()
    async def pause(self, interaction: Interaction):
        """Pause the current track."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        await player.pause(True)
        await interaction.response.send_message(
            "⏸️ Воспроизведение приостановлено", ephemeral=True, silent=True
        )
        logger.info(f"Playback paused for guild {interaction.guild_id}")

    @app_commands.command(name="resume", description="Возобновить воспроизведение")
    @app_commands.guild_only()
    @handle_errors()
    async def resume(self, interaction: Interaction):
        """Resume paused playback."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        await player.pause(False)
        await interaction.response.send_message(
            "▶️ Воспроизведение возобновлено", ephemeral=True, silent=True
        )
        logger.info(f"Playback resumed for guild {interaction.guild_id}")

    @app_commands.command(
        name="queue",
        description="Показать текущую очередь (ephemeral скрывает сообщение)",
    )
    @app_commands.describe(ephemeral="Скрывает ваше сообщение от всех (если True)")
    @app_commands.guild_only()
    @handle_errors()
    async def queue(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = False,
    ):
        """Display the current playback queue."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        if not player.queue:
            logger.debug(f"Queue is empty for guild {interaction.guild_id}")
            return await interaction.response.send_message(
                "ℹ️ Очередь пуста", ephemeral=True
            )
        embed = discord.Embed(title="Очередь воспроизведения", color=0xFFAE00)
        if player.is_playing:
            embed.add_field(
                name="Сейчас играет",
                value=f"[{player.queue[0]}]({player.queue[0].uri})",
                inline=False,
            )
        queue_text = "\n".join(
            f"{idx + 1}. [{track.title}]({track.uri})"
            for idx, track in enumerate(player.queue[1:10])
        )
        if len(player.queue) > 10:
            queue_text += f"\n... (+{len(player.queue) - 10} остальных)"
        if queue_text:
            embed.add_field(name="Далее", value=queue_text, inline=False)
        embed.set_footer(text=f"Всего треков: {len(player.queue)}")
        await interaction.response.send_message(
            embed=embed, ephemeral=ephemeral, silent=True
        )
        logger.info(f"Queue displayed for guild {interaction.guild_id}")

    @app_commands.command(
        name="volume", description="Установить громкость воспроизведения (0-200)"
    )
    @app_commands.describe(volume="Уровень громкости (0-200)")
    @app_commands.guild_only()
    @handle_errors()
    async def volume(
        self, interaction: Interaction, volume: app_commands.Range[int, 0, 200]
    ):
        """Adjust playback volume."""
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        await player.volume(volume)
        await self._set_volume(interaction.guild_id, volume)  # type: ignore
        await interaction.response.send_message(
            f"🔊 Громкость установлена на {volume}%", ephemeral=True, silent=True
        )
        logger.info(f"Volume set to {volume}% for guild {interaction.guild_id}")

    @app_commands.command(name="leave", description="Покинуть голосовой канал")
    @app_commands.guild_only()
    @handle_errors()
    async def leave(self, interaction: Interaction):
        """Disconnect from voice channel."""
        player = await self._get_player_or_handle_error(interaction, needs_player=False)
        if player is None and (self.node is None or interaction.guild_id is None):
            return
        if player:
            logger.info(f"Destroying player for guild {interaction.guild_id}.")
            await player.destroy()

        if not interaction.guild or not interaction.guild.voice_client:
            logger.debug("Bot is not connected to a voice channel during leave")
            return await interaction.response.send_message(
                "Не в голосовом канале", ephemeral=True, silent=True
            )
        await interaction.guild.voice_client.disconnect(force=True)
        await interaction.response.send_message(
            "ℹ️ Покинул голосовой канал", ephemeral=True, silent=True
        )
        logger.info(f"Left voice channel for guild {interaction.guild_id}")

    @app_commands.command(
        name="rotate-queue",
        description="Пропускает текущий трек и добавляет его в конец.",
    )
    @app_commands.guild_only()
    @handle_errors()
    async def rotate(self, interaction: Interaction):
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return

        current_track = player.queue[0] if player.queue else None
        if current_track is None:
            return await interaction.response.send_message(
                "Очередь пуста", ephemeral=True
            )
        requester = current_track.requester
        try:
            requester = int(requester if requester else "0")
        except ValueError:
            requester = 0
        await player.play(current_track, requester=requester)
        await player.skip()
        await interaction.response.send_message(
            f"🔄 Текущий трек  [{current_track.title}]({current_track.uri}) "
            "пропущен и перемещён в конец",
            delete_after=15,
            silent=True,
        )
        logger.info(
            (
                f"Rotated queue for guild {interaction.guild_id}. "
                f"Current track URI: {getattr(player.queue[0], 'uri', 'N/A')}"
            )
        )


class LavalinkVoiceClient(discord.VoiceClient):
    """A voice client for Lavalink.
    https://discordpy.readthedocs.io/en/latest/api.html#voiceprotocol.
    """

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        # super().__init__(client, channel)
        logger.debug("[INIT] Creating voice client...")
        try:
            self.client = client
            self.channel = channel  # type: ignore
            music_cog: MusicCog = self.client.get_cog("MusicCog")  # type: ignore
            if not isinstance(music_cog, MusicCog):
                raise RuntimeError("MusicCog not loaded!")

            self.lavalink = music_cog.node
            logger.debug("[INIT] Lavalink assigned; Lavalink: %s", self.lavalink)
        except Exception as e:
            logger.exception("Unexpected error in voice client init: %s", e)

    async def on_voice_server_update(self, data: dict[str, str]):  # pyright: ignore[reportIncompatibleMethodOverride]
        logger.debug("[VOICE SERVER UPDATE] Received data: %s", data)
        if self.lavalink is None:
            logger.exception("Voice error occurred: lavalink is None", exc_info=True)
            return
        player = cast(
            None | lavaplay.player.Player,
            self.lavalink.get_player(self.channel.guild.id),
        )
        if player is None:
            logger.exception("Voice error occurred: player is None", exc_info=True)
            return
        await player.raw_voice_server_update(
            data.get("endpoint", "missing"), data.get("token", "missing")
        )

    async def on_voice_state_update(self, data: dict[str, str]):  # pyright: ignore[reportIncompatibleMethodOverride]
        logger.debug("[VOICE STATE UPDATE] Received data: %s", data)
        if self.lavalink is None:
            logger.exception("Voice error occurred: lavalink is None", exc_info=True)
            return

        player = cast(
            None | lavaplay.player.Player,
            self.lavalink.get_player(self.channel.guild.id),
        )
        channel_id = cast(
            str | int | None,
            data["channel_id"],  # channel_id might be None
        )

        if player is None:
            logger.exception("Voice error occurred: player is None", exc_info=True)
            return
        if channel_id is None:
            await self.disconnect(force=True)
            await player.raw_voice_state_update(
                int(data["user_id"]),
                data["session_id"],
                channel_id,
            )
            return
        channel_id = int(channel_id)
        if channel_id != self.channel.id:
            channel = self.client.get_channel(channel_id)
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                self.channel = channel
            await self.connect(timeout=5.0, reconnect=True)
        await player.raw_voice_state_update(
            int(data["user_id"]),
            data["session_id"],
            channel_id,
        )

    async def move_to(
        self, channel: Snowflake | None, *, timeout: float | None = 30
    ) -> None:
        if channel is None:
            await self.disconnect(force=True)
            return

        if self.channel and channel.id == self.channel.id:
            return
        await self.channel.guild.change_voice_state(channel=channel)

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        logger.debug("[CONNECT] Attempting to connect to %s...", self.channel)
        await self.channel.guild.change_voice_state(
            channel=self.channel, self_mute=self_mute, self_deaf=self_deaf
        )

    async def disconnect(self, *, force: bool = False) -> None:
        logger.debug("[DISCONNECT] Attempting to disconnect voice client...")
        await self.channel.guild.change_voice_state(channel=None)
        self.cleanup()


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(MusicCog(bot))
