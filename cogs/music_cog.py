# -*- coding: utf-8 -*-
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

from __future__ import annotations

import asyncio
import functools
import logging
import time
from datetime import timedelta
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Concatenate,
    Coroutine,
    Final,
    Self,
    TypedDict,
    cast,
    override,
)

import discord
import lavaplay  # type: ignore
from discord import (
    Interaction,
    Member,
    StageChannel,
    VoiceChannel,
    app_commands,
)
from discord.abc import Snowflake
from discord.ext import commands, tasks
from discord.utils import format_dt, utcnow
from lavaplay.player import Player  # type: ignore

import config
from utils import BaseCog, FailureUI, get_json, save_json

logger = logging.getLogger("MusicCog")

type AsyncFunc[T, **P] = Callable[P, Awaitable[T]]
type VocalGuildChannel = StageChannel | VoiceChannel
type VoiceCheckData = (
    VocalGuildChannel | tuple[VocalGuildChannel, VocalGuildChannel] | None
)
type MusicCommand[CogT: MusicCog, T, **P] = Callable[
    Concatenate[CogT, Interaction, P],
    Coroutine[Any, Any, T],
]


class RepeatMode(Enum):
    off = "off"
    queue = "queue"


class VoiceCheckResult(Enum):
    ALREADY_CONNECTED = ("Уже подключён к {0}", True)
    CHANNEL_EMPTY = ("Голосовой канал {0} пуст!", False)
    CONNECTION_FAILED = ("Ошибка подключения к {0}", False)
    INVALID_CHANNEL_TYPE = ("Неверный тип голосового канала", False)
    MOVED_CHANNELS = ("Переместился {0} -> {1}", True)
    SUCCESS = ("Успешно подключился к {0}", True)
    USER_NOT_IN_VOICE = ("Вы должны быть в голосовом канале!", False)
    USER_NOT_MEMBER = ("Неверный тип пользователя", False)

    def __init__(self, msg: str, is_success: bool):
        self._msg = msg
        self._is_success = is_success

    @property
    def msg(self) -> str:
        return self._msg

    @property
    def is_success(self) -> bool:
        return self._is_success


class EmptyTimerInfo(TypedDict):
    """Information about an empty channel timer.

    Attributes:
        timestamp: Unix timestamp when the timer started
        reason: Why the timer was started ("empty" or "all_deafened")

    """

    timestamp: float
    reason: str | None


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


def handle_errors[CogT: MusicCog, T, **P]() -> Callable[
    [MusicCommand[CogT, T, P]], MusicCommand[CogT, T | None, P]
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

    def decorator(func: MusicCommand[CogT, T, P]) -> MusicCommand[CogT, T | None, P]:
        @functools.wraps(func)
        async def wrapper(
            self: CogT,
            interaction: Interaction,
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> T | None:
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

        return cast(MusicCommand[CogT, T | None, P], wrapper)

    return decorator


class MusicCog(BaseCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.lavalink = lavaplay.Lavalink()
        self.node: lavaplay.Node | None = None
        self.empty_channel_timers: dict[int, EmptyTimerInfo] = {}

    @override
    async def cog_unload(self) -> None:
        if hasattr(self, "auto_leave_monitor") and self.auto_leave_monitor.is_running():
            self.auto_leave_monitor.cancel()

        if self.node is not None:
            await self.node.close()
        for node in self.lavalink.nodes:
            self.lavalink.destroy_node(node)

    @override
    async def cog_load(self):
        if self.bot.is_ready():
            await self.initialize_node()

        self.auto_leave_monitor.start()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.initialize_node()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Monitor voice state changes for auto-leave feature."""
        if not self.bot.user:
            return

        guild = member.guild
        if not guild.voice_client or not isinstance(
            guild.voice_client, LavalinkVoiceClient
        ):
            return

        bot_channel = guild.voice_client.channel
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

    async def _update_channel_timer(self, guild_id: int, channel: VocalGuildChannel):
        """Update the empty channel timer for a specific guild."""
        human_members = [m for m in channel.members if not m.bot]

        effectively_empty = False
        empty_reason: str | None = None

        if len(human_members) == 0:
            effectively_empty = True
            empty_reason = "empty"
        else:
            listening_users: list[discord.Member] = []
            for member in human_members:
                if member.voice and not (member.voice.deaf or member.voice.self_deaf):
                    listening_users.append(member)

            if len(listening_users) == 0:
                effectively_empty = True
                empty_reason = "all_deafened"

        if effectively_empty:
            if guild_id not in self.empty_channel_timers:
                self.empty_channel_timers[guild_id] = EmptyTimerInfo(
                    timestamp=time.monotonic(),
                    reason=empty_reason,
                )
                logger.info(
                    msg="Started empty timer: "
                    f"guild={guild_id}, channel={channel.name}, reason={empty_reason}"
                )
        else:
            if guild_id in self.empty_channel_timers:
                del self.empty_channel_timers[guild_id]
                logger.info(
                    "Stopped empty timer: "
                    f"guild={guild_id}, channel={channel.name}, reason=users_listening"
                )

    @tasks.loop(seconds=config.MUSIC_AUTO_LEAVE_CHECK_INTERVAL)
    async def auto_leave_monitor(self):
        """Background task that monitors empty channels and triggers auto-leave."""
        current_time = time.monotonic()
        guilds_to_leave: list[int] = []

        for guild_id, timer_info in self.empty_channel_timers.items():
            empty_since = timer_info["timestamp"]
            if current_time - empty_since >= config.MUSIC_AUTO_LEAVE_TIMEOUT:
                guilds_to_leave.append(guild_id)

        for guild_id in guilds_to_leave:
            try:
                await self._auto_leave_guild(guild_id)
            except Exception as e:
                logger.exception(f"Error during auto-leave for guild {guild_id}: {e}")

    async def _auto_leave_guild(self, guild_id: int):
        """Automatically leave voice channel for a specific guild."""
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            self.empty_channel_timers.pop(guild_id, None)
            return

        vc = guild.voice_client
        if not isinstance(vc, LavalinkVoiceClient):
            self.logger.error("Unexpected voice client type: %s", type(vc))
            return

        channel_name = vc.channel.name
        info = self.empty_channel_timers[guild_id]
        reason = info["reason"]

        logger.info(
            f"Auto-leaving: guild={guild_id}, channel={channel_name}, reason={reason}"
        )
        if self.node:
            player = self.node.get_player(guild_id)
            if player:
                await player.destroy()
        await vc.disconnect(force=True)
        self.empty_channel_timers.pop(guild_id, None)

    async def initialize_node(self):
        """Safely initialize Lavalink node."""
        if self.node and self.node.is_connect:
            return
        await self._connect_node()

    async def _get_player(self, guild_id: int) -> Player:
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
                    host=config.LAVALINK_HOST,
                    port=config.LAVALINK_PORT,
                    password=config.LAVALINK_PASSWORD,
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
        volume_data = get_json(config.MUSIC_VOLUME_FILE) or {}
        return volume_data.get(str(guild_id), config.MUSIC_DEFAULT_VOLUME)

    async def _set_volume(self, guild_id: int, volume: int):
        """Save volume for specific guild."""
        volume_data = get_json(config.MUSIC_VOLUME_FILE) or {}
        volume_data[str(guild_id)] = volume
        save_json(config.MUSIC_VOLUME_FILE, volume_data)

    async def send_response(
        self,
        interaction: Interaction,
        content: str = "",
        *,
        delete_after: float | None = None,
        ephemeral: bool = False,
        silent: bool = True,
        embed: discord.Embed | None = None,
        title: str | None = None,
        description: str | None = None,
        color: int = 0xFFAE00,
    ) -> None:
        """Send response with optional embed creation.

        Args:
            interaction: The discord Interaction to respond to.
            content: Text content (used if no embed provided)
            delete_after: Time in seconds after which the message should be deleted.
            ephemeral: Whether the response should be ephemeral.
            silent: If True, attempt to send the response silently where supported.
            embed: Pre-built embed to send
            title: Quick embed title (creates embed if provided)
            description: Quick embed description (creates embed if provided)
            color: Embed color (default: orange/gold 0xFFAE00)

        """
        if not embed and (title or description):
            embed = discord.Embed(color=color)
            if title:
                embed.title = title
            if description:
                embed.description = description
        timer = ""
        if delete_after:
            expire_at = utcnow() + timedelta(seconds=delete_after)
            timer = f"-# Удалится {format_dt(expire_at, style='R')}"
        if embed and timer:
            embed.add_field(name="", value=timer, inline=False)
        kwargs: dict[str, Any] = {
            "content": (content + timer) if content else None,
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
    ) -> Player | None:
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
        await self.send_response(
            interaction,
            description=message,
            color=config.Color.SUCCESS if result.is_success else config.Color.ERROR,
        )

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
                delete_after=120,
            )
            return
        if not await self._check_and_reconnect_node():
            await FailureUI.send_failure(
                interaction,
                title="Ошибка сервиса",
                description="Музыкальный сервис недоступен. Попробуйте выйти и зайти.",
                delete_after=120,
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
                "Трек не найден",
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
                title="Результаты не найдены",
                delete_after=30,
            )

    async def _handle_track(
        self,
        interaction: Interaction,
        player: Player,
        track: lavaplay.Track,
    ):
        try:
            await player.play(track, requester=interaction.user.id)
            embed = discord.Embed(
                title="Трек добавлен в очередь"
                if len(player.queue) > 1
                else "Сейчас играет",
                description=f"[{track.title}]({track.uri})",
                color=config.Color.MUSIC,
            )
            if track.length:
                formatted_length = self.format_time(track.length // 1000)
                embed.add_field(
                    name="Длительность",
                    value=formatted_length,
                    inline=True,
                )

            requester = (
                interaction.guild.get_member(interaction.user.id)
                if interaction.guild
                else None
            )
            if track.artworkUrl:
                embed.set_thumbnail(url=track.artworkUrl)
            if requester:
                embed.set_footer(
                    text=f"Запросил: {requester.display_name}",
                    icon_url=requester.display_avatar.url,
                )

            await self.send_response(
                interaction,
                embed=embed,
                delete_after=min(300, track.length // 1000 + 10),
                silent=True,
            )
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

    def format_time(self, seconds: int) -> str:
        """Formats a given number of seconds as a string in the format
        DD days, HH:MM:SS.

        Args:
            seconds (int): The number of seconds to format.

        Returns:
            str: The formatted string.

        """
        return str(timedelta(seconds=seconds))

    async def _handle_playlist(
        self,
        interaction: Interaction,
        player: Player,
        playlist: lavaplay.PlayList,
    ):
        try:
            await player.play_playlist(playlist)
            total_sec = sum(t.length if t else 0 for t in playlist.tracks) // 1000
            embed = discord.Embed(
                title=f"Добавлен плейлист **{playlist.name}**",
                description=f"Треков: {len(playlist.tracks)} шт.",
                color=config.Color.MUSIC,
            )
            embed.add_field(
                name="",
                value=f"Общая длительность: {self.format_time(total_sec)}",
                inline=True,
            )
            if playlist.tracks and playlist.tracks[0].artworkUrl:
                embed.set_thumbnail(url=playlist.tracks[0].artworkUrl)
            embed.set_footer(
                text=f"Запросил: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )
            await self.send_response(
                interaction,
                embed=embed,
                delete_after=min(600, total_sec + 10),
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
            if not isinstance(voice_client, LavalinkVoiceClient):
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
        await player.stop()
        await self.send_response(
            interaction,
            title="Остановлено",
            description="Воспроизведение остановлено, очередь очищена",
            color=config.Color.INFO,
            delete_after=60,
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
            await self.send_response(
                interaction,
                title="Пустая очередь",
                description="Нечего пропускать.",
                color=config.Color.WARNING,
                ephemeral=True,
            )
            return
        current = player.queue[0]
        next_track = player.queue[1] if len(player.queue) > 1 else None
        await player.skip()
        embed = discord.Embed(
            title="Трек пропущен",
            color=config.Color.INFO,
        )
        embed.add_field(
            name="Пропущен", value=f"[{current.title}]({current.uri})", inline=False
        )

        if next_track:
            embed.add_field(
                name="Следующий",
                value=f"[{next_track.title}]({next_track.uri})",
                inline=False,
            )
            if next_track.artworkUrl:
                embed.set_thumbnail(url=next_track.artworkUrl)
        else:
            embed.add_field(name="Очередь", value="Пуста", inline=False)

        await self.send_response(interaction, embed=embed, delete_after=60, silent=True)
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
        await self.send_response(
            interaction,
            title="Пауза",
            description="Воспроизведение приостановлено",
            color=config.Color.INFO,
            delete_after=120,
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
        await self.send_response(
            interaction,
            title="Возобновлено",
            description="Воспроизведение возобновлено",
            color=config.Color.INFO,
            delete_after=120,
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
        guild = await self._require_guild(interaction)
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        if not player.queue:
            logger.debug(f"Queue is empty for guild {guild.id}")
            return await self.send_response(
                interaction,
                title="Очередь пуста",
                color=config.Color.WARNING,
                ephemeral=True,
            )

        view = QueuePaginator(interaction.user.id, player)
        await view.send(interaction, ephemeral=ephemeral)
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
        guild = await self._require_guild(interaction)
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return
        await player.volume(volume)
        await self._set_volume(guild.id, volume)
        await self.send_response(
            interaction,
            title=f"🔊 Громкость установлена на {volume}%",
            silent=True,
            delete_after=120,
        )
        logger.info(f"Volume set to {volume}% for guild {guild.id}")

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
            return await self.send_response(
                interaction,
                title="Не в голосовом канале",
                color=config.Color.INFO,
                ephemeral=True,
            )

        await interaction.guild.voice_client.disconnect(force=True)
        await self.send_response(
            interaction,
            title="До свидания 💖",
            description="Покинул голосовой канал",
            color=config.Color.INFO,
            delete_after=120,
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
            return await self.send_response(
                interaction,
                title="Очередь пуста",
                color=config.Color.WARNING,
                ephemeral=True,
            )
        requester = current_track.requester
        try:
            requester = int(requester if requester else "0")
        except ValueError:
            requester = 0
        await player.play(current_track, requester=requester)
        await player.skip()
        embed = discord.Embed(
            title="Очередь сдвинута",
            description=f"Трек [{current_track.title}]({current_track.uri}) "
            "перемещён в конец очереди",
            color=config.Color.INFO,
        )
        if current_track.artworkUrl:
            embed.set_thumbnail(url=current_track.artworkUrl)
        embed.set_footer(
            text=f"Новая позиция: {player.queue[0].title if player.queue else 'Пусто'}"
        )
        await self.send_response(
            interaction,
            embed=embed,
            delete_after=120,
            silent=True,
        )
        logger.info(
            (
                f"Rotated queue for guild {interaction.guild_id}. "
                f"Current track URI: {getattr(player.queue[0], 'uri', 'N/A')}"
            )
        )

    @app_commands.command(
        name="repeat",
        description="Включить/выключить повтор.",
    )
    @app_commands.describe(mode="off — выкл, queue — повтор очереди")
    @app_commands.guild_only()
    @handle_errors()
    async def repeat(self, interaction: Interaction, mode: RepeatMode | None = None):
        player = await self._get_player_or_handle_error(interaction)
        if player is None:
            return

        current = RepeatMode.queue if player._queue_repeat else RepeatMode.off  # pyright: ignore[reportPrivateUsage]

        if mode is None:
            mode = RepeatMode.off if current == RepeatMode.queue else RepeatMode.queue
        match mode:
            case RepeatMode.off:
                player.queue_repeat(False)
                msg = "Повтор **отключён**"
            case RepeatMode.queue:
                player.queue_repeat(True)
                msg = "Повтор очереди **включён**"

        await self.send_response(
            interaction,
            title="Залупливание",
            description=msg,
            color=config.Color.WARNING
            if current == RepeatMode.off
            else config.Color.SUCCESS,
            delete_after=120,
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

    @override
    async def on_voice_server_update(self, data: dict[str, str]):  # pyright: ignore[reportIncompatibleMethodOverride]
        logger.debug("[VOICE SERVER UPDATE] Received data: %s", data)
        if self.lavalink is None:
            logger.exception("Voice error occurred: lavalink is None", exc_info=True)
            return
        player = cast(
            None | Player,
            self.lavalink.get_player(self.channel.guild.id),
        )
        if player is None:
            logger.exception("Voice error occurred: player is None", exc_info=True)
            return
        await player.raw_voice_server_update(
            data.get("endpoint", "missing"), data.get("token", "missing")
        )

    @override
    async def on_voice_state_update(self, data: dict[str, str]):  # pyright: ignore[reportIncompatibleMethodOverride]
        logger.debug("[VOICE STATE UPDATE] Received data: %s", data)
        if self.lavalink is None:
            logger.exception("Voice error occurred: lavalink is None", exc_info=True)
            return

        player = cast(
            None | Player,
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

    @override
    async def move_to(
        self, channel: Snowflake | None, *, timeout: float | None = 30
    ) -> None:
        if channel is None:
            await self.disconnect(force=True)
            return

        if self.channel and channel.id == self.channel.id:
            return
        await self.channel.guild.change_voice_state(channel=channel)

    @override
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

    @override
    async def disconnect(self, *, force: bool = False) -> None:
        logger.debug("[DISCONNECT] Attempting to disconnect voice client...")
        await self.channel.guild.change_voice_state(channel=None)
        self.cleanup()


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

        # explicit buttons, just to individually disable them
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
        """Enable/disable navigation buttons based on current page and total pages."""
        pages = self._pages_count()

        self.first_btn.disabled = self.page == 0 or pages == 1
        self.prev_btn.disabled = self.page == 0 or pages == 1
        self.next_btn.disabled = self.page >= pages - 1 or pages == 1
        self.last_btn.disabled = self.page >= pages - 1 or pages == 1

    def _make_embed(self) -> discord.Embed:
        q = self.player.queue
        embed = discord.Embed(title="Очередь воспроизведения", color=0xFFAE00)
        if q:
            now = q[0]
            embed.add_field(
                name="Сейчас играет", value=f"[{now.title}]({now.uri})", inline=False
            )
        start = 1 + self.page * self.page_size
        end = min(len(q), start + self.page_size)
        if start < len(q):
            lines = [
                f"{idx}. [{track.title}]({track.uri})"
                for idx, track in enumerate(q[start:end], start=start)
            ]
            if lines:
                embed.add_field(name="Далее", value="\n".join(lines), inline=False)

        mode = self.player._queue_repeat  # pyright: ignore[reportPrivateUsage]
        embed.set_footer(
            text=f"Стр. {self.page + 1}/{self._pages_count()}"
            f" • Всего: {len(q)}"
            f" • Повтор: {'вкл.' if mode else 'выкл.'}"
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

    async def first(self, interaction: Interaction):
        self.page = 0
        await self._update_view(interaction)

    async def prev(self, interaction: Interaction):
        self.page = max(self.page - 1, 0)
        await self._update_view(interaction)

    async def next(self, interaction: Interaction):
        self.page = min(self.page + 1, self._pages_count() - 1)
        await self._update_view(interaction)

    async def last(self, interaction: Interaction):
        self.page = self._pages_count() - 1
        await self._update_view(interaction)

    async def update(self, interaction: Interaction):
        self.page = 0
        await self._update_view(interaction)
        logger.debug(f"Queue refreshed for guild {self.player.guild_id}")

    async def close(self, interaction: Interaction):
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
