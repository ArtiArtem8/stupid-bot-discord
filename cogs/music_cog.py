# -*- coding: utf-8 -*-
import asyncio
import logging
import os

import discord
import lavaplay
import lavaplay.player
from discord import Interaction, app_commands
from discord.ext import commands

from config import MUSIC_DEFAULT_VOLUME

# Load environment variables
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", 2333))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

logger = logging.getLogger("MusicCog")


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._current_volume = MUSIC_DEFAULT_VOLUME
        self.lavalink = lavaplay.Lavalink()
        self.node = None

    async def cog_unload(self) -> None:
        await self.node.close()
        for i in self.lavalink.nodes:
            await i.close()
        self.lavalink.nodes = list()

    async def cog_load(self):
        if self.bot.is_ready():
            await self.initialize_node()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.initialize_node()

    async def initialize_node(self):
        """Safely initialize Lavalink node"""
        if self.node and self.node.is_connect:
            return
        await self._connect_node()

    async def _get_player(self, guild_id: int) -> lavaplay.player.Player:
        """Get existing player or create new one with proper voice data"""
        await self.initialize_node()  # Ensure node is connected
        player = self.node.get_player(guild_id)
        if not player:
            player = self.node.create_player(guild_id)
        return player

    async def _connect_node(self):
        """Full node connection sequence"""
        try:
            if self.lavalink.nodes:
                self.node = self.lavalink.default_node
            else:
                self.node = self.lavalink.create_node(
                    host=LAVALINK_HOST,
                    port=LAVALINK_PORT,
                    password=LAVALINK_PASSWORD,
                    user_id=self.bot.user.id,
                )
            self.node.set_event_loop(self.bot.loop)
            self.node.connect()
            await asyncio.wait_for(self._wait_for_connection(), timeout=10)
            logger.info("Node connected successfully")
        except Exception as e:
            logger.error("Node connection failed: %s", e)
            raise

    async def _check_and_reconnect_node(self) -> bool:
        """Verify Lavalink node connection"""
        if not self.node.is_connect:
            logger.warning("Lavalink node not connected")
            try:
                await self._connect_node()
                return True
            except Exception as e:
                logger.error("Node reconnect failed: %s", e)
                return False
        return True

    async def _wait_for_connection(self):
        """Wait until node is fully connected"""
        while not self.node.is_connect:
            await asyncio.sleep(0.1)

    @app_commands.command(name="join", description="Присоединиться к голосовому каналу")
    async def join(
        self, interaction: Interaction, *, channel: discord.VoiceChannel = None
    ):
        """Join your current voice channel"""
        await interaction.response.defer(ephemeral=True)
        try:
            if not await self._ensure_voice(interaction, None):
                return

            success_message = (
                f"✅ Присоединился к {interaction.user.voice.channel.mention}"
            )
            logger.info(success_message)
            await interaction.followup.send(
                success_message, ephemeral=True, silent=True
            )

        except discord.ClientException as e:
            error_message = "❌ Уже подключён к голосовому каналу!"
            await interaction.followup.send(error_message, ephemeral=True)
            logger.error("Discord ClientException error in join: %s", e)
        except Exception as e:
            error_message = "❌ Произошла непредвиденная ошибка при подключении."
            await interaction.followup.send(error_message, ephemeral=True)
            logger.exception("Unexpected error in join command: %s", e)

    @app_commands.command(
        name="play",
        description="Воспроизведение музыки с YouTube, SoundCloud и Yandex Music",
    )
    @app_commands.describe(query="Название трека или URL")
    async def play(
        self,
        interaction: Interaction,
        *,
        query: str,
        channel: discord.VoiceChannel = None,
        ephemeral: bool = False,
    ):
        """Play a song from various supported platforms"""
        # TODO: проверит доступ к каналу по пользователю
        try:
            await interaction.response.defer(ephemeral=ephemeral)
            if not await self._ensure_voice(interaction, None):
                return

            if not await self._check_and_reconnect_node():
                await interaction.followup.send(
                    "❌ Audio service unavailable, reconnecting..."
                )

            player = await self._get_player(interaction.guild_id)
            await player.volume(self._current_volume)
            tracks = await self.node.auto_search_tracks(query)

            if isinstance(tracks, lavaplay.PlayList):
                logger.debug("Playlist found: %s tracks", len(tracks.tracks))
                await self._handle_playlist(interaction, player, tracks)
            elif isinstance(tracks, list) and len(tracks) > 0:
                logger.debug("Single track found: %s", tracks[0].title)
                await self._handle_track(interaction, player, tracks[0])
            else:
                logger.debug("No track results found for query: %s", query)
                await interaction.followup.send("❌ Результаты не найдены")
        except Exception as e:
            logger.exception("Unexpected error in play command: %s", e)
            await interaction.followup.send("❌ Ёбаный ютуб опять сломался.")

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
                    title="✅ Трек добавлен в очередь unecesary",
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
            await interaction.followup.send("❌ Ошибка загрузки трека: %s", e)
        except Exception as e:
            logger.exception("Unexpected error in _handle_track: %s", e)
            await interaction.followup.send(
                "❌ Произошла ошибка во время воспроизведения трека",
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
                f"🎶 Плейлист **{playlist.name}** с {len(playlist.tracks)} треками добавлен в очередь",
                silent=True,
            )
        except Exception as e:
            logger.exception("Unexpected error in _handle_playlist: %s", e)
            await interaction.followup.send(
                "❌ Произошла ошибка при добавлении плейлиста в очередь"
            )

    async def _ensure_voice(
        self, interaction: Interaction, channel: discord.VoiceChannel = None
    ) -> bool:
        """Ensure bot is connected to voice channel"""
        if channel is None and not interaction.user.voice:
            await interaction.followup.send(
                "❌ Вы должны быть в голосовом канале!", ephemeral=True
            )
            return False
        channel = channel or interaction.user.voice.channel

        if not interaction.guild.voice_client:
            try:
                if not any(channel.members):
                    await interaction.followup.send(
                        "❌ Голосовой канал пуст!", ephemeral=True
                    )
                    return False
                await channel.connect(cls=LavalinkVoiceClient, self_deaf=True)
                self.node.create_player(interaction.guild_id)
            except Exception as e:
                logger.exception(
                    "Error connecting bot to voice channel in _ensure_voice: %s", e
                )
                await interaction.followup.send(
                    "❌ Не удалось подключиться к голосовому каналу", ephemeral=True
                )
                return False
        return True

    @app_commands.command(
        name="stop", description="Остановить воспроизведение и очистить очередь"
    )
    async def stop(self, interaction: Interaction):
        """Stop the player and clear queue"""
        try:
            logger.debug("Stop command invoked")
            player = self.node.get_player(interaction.guild_id)
            if not player:
                logger.debug("Player not found in stop command")
                return await interaction.response.send_message(
                    "❌ Не удалось остановить воспроизведение", ephemeral=True
                )
            await player.stop()
            player.queue.clear()
            await interaction.response.send_message(
                "Воспроизведение остановлено, очередь очищена",
                delete_after=15,
                silent=True,
            )
            logger.info("Playback stopped and queue cleared")
        except Exception as e:
            logger.exception("Unexpected error in stop command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при остановке воспроизведения", ephemeral=True
            )

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    async def skip(self, interaction: Interaction):
        """Skip to the next track in queue"""
        try:
            logger.debug("Skip command invoked")
            player = self.node.get_player(interaction.guild_id)
            if not player:
                logger.debug("Player not found in skip command")
                return await interaction.response.send_message(
                    "❌ Не удалось пропустить трек", ephemeral=True
                )
            await player.skip()
            await interaction.response.send_message(
                "⏭️ Текущий трек пропущен", delete_after=15, silent=True
            )
            logger.info("Track skipped")
        except Exception as e:
            logger.exception("Unexpected error in skip command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при пропуске трека", ephemeral=True
            )

    @app_commands.command(
        name="pause", description="Поставить воспроизведение на паузу"
    )
    async def pause(self, interaction: Interaction):
        """Pause the current track"""
        try:
            logger.debug("Pause command invoked")
            player = self.node.get_player(interaction.guild_id)
            if not player:
                logger.debug("Player not found in pause command")
                return await interaction.response.send_message(
                    "❌ Не удалось приостановить воспроизведение", ephemeral=True
                )
            await player.pause(True)
            await interaction.response.send_message(
                "⏸️ Воспроизведение приостановлено", ephemeral=True, silent=True
            )
            logger.info("Playback paused")
        except Exception as e:
            logger.exception("Unexpected error in pause command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при постановке на паузу", ephemeral=True
            )

    @app_commands.command(name="resume", description="Возобновить воспроизведение")
    async def resume(self, interaction: Interaction):
        """Resume paused playback"""
        try:
            logger.debug("Resume command invoked")
            player = self.node.get_player(interaction.guild_id)
            if not player:
                logger.debug("Player not found in resume command")
                return await interaction.response.send_message(
                    "❌ Не удалось возобновить воспроизведение", ephemeral=True
                )
            await player.pause(False)
            await interaction.response.send_message(
                "▶️ Воспроизведение возобновлено", ephemeral=True, silent=True
            )
            logger.info("Playback resumed")
        except Exception as e:
            logger.exception("Unexpected error in resume command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при возобновлении воспроизведения", ephemeral=True
            )

    @app_commands.command(name="queue", description="Показать текущую очередь")
    async def queue(
        self,
        interaction: Interaction,
        *,
        ephemeral: bool = False,
    ):
        """Display the current playback queue"""
        try:
            logger.debug("Queue command invoked")
            await self._check_and_reconnect_node()
            player = await self._get_player(interaction.guild_id)
            if not player or not player.queue:
                logger.debug("Queue is empty")
                return await interaction.response.send_message(
                    "❌ Очередь пуста", ephemeral=True
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

            await interaction.response.send_message(
                embed=embed, ephemeral=ephemeral, silent=True
            )
            logger.info("Queue displayed")
        except Exception as e:
            logger.exception("Unexpected error in queue command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при выводе очереди", ephemeral=True
            )

    @app_commands.command(
        name="volume", description="Установить громкость воспроизведения (0-200)"
    )
    @app_commands.describe(volume="Уровень громкости (0-200)")
    async def volume(
        self, interaction: Interaction, volume: app_commands.Range[int, 0, 200]
    ):
        """Adjust playback volume"""
        try:
            logger.debug("Volume command invoked with volume: %d", volume)
            player = self.node.get_player(interaction.guild_id)
            if player:
                await player.volume(volume)
            self._current_volume = volume
            await interaction.response.send_message(
                f"🔊 Громкость установлена на {volume}%", ephemeral=True, silent=True
            )
            logger.info("Volume set to %d", volume)
        except Exception as e:
            logger.exception("Unexpected error in volume command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при установке громкости", ephemeral=True
            )

    @app_commands.command(name="leave", description="Покинуть голосовой канал")
    async def leave(self, interaction: Interaction):
        """Disconnect from voice channel"""
        try:
            logger.debug("Leave command invoked")
            player = self.node.get_player(interaction.guild_id)
            if player and player.is_connected:
                await player.destroy()
            if not interaction.guild.voice_client:
                logger.debug("Bot is not connected to a voice channel during leave")
                return await interaction.response.send_message(
                    "Не в голосовом канале", ephemeral=True, silent=True
                )
            await interaction.guild.voice_client.disconnect(force=True)
            await interaction.response.send_message(
                "Покинул голосовой канал", ephemeral=True, silent=True
            )
            logger.info("Left voice channel")
        except Exception as e:
            logger.exception("Unexpected error in leave command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при выходе из голосового канала", ephemeral=True
            )

    @app_commands.command(
        name="rotate-queue",
        description="Пропускает текущий трек и добавляет его в конец.",
    )
    async def rotate(self, interaction: Interaction):
        try:
            logger.debug("Rotate command invoked")
            player = self.node.get_player(interaction.guild_id)
            if not player:
                logger.debug("Player not found in skip command")
                return await interaction.response.send_message(
                    "❌ Не удалось пропустить трек", ephemeral=True
                )
            current_track = player.queue[0] if player.queue else None
            if current_track is None:
                return await interaction.response.send_message(
                    "❌ Очередь пуста", ephemeral=True
                )
            await player.play(current_track, requester=current_track.requester)
            await player.skip()
            await interaction.response.send_message(
                "⏭️ Текущий трек пропущен и перемещён в конец",
                delete_after=15,
                silent=True,
            )
            logger.info(
                "Track rotated %s: %s",
                current_track.uri,
                " | ".join(list(t.uri for t in player.queue)),
            )
        except Exception as e:
            logger.exception("Unexpected error in skip command: %s", e)
            await interaction.response.send_message(
                "❌ Произошла ошибка при ротации трека", ephemeral=True
            )


class LavalinkVoiceClient(discord.VoiceClient):
    """
    A voice client for Lavalink.
    https://discordpy.readthedocs.io/en/latest/api.html#voiceprotocol
    """

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        logger.debug("[INIT] Creating voice client...")
        try:
            self.client = client
            self.channel = channel
            music_cog: MusicCog = self.client.get_cog("MusicCog")
            if not music_cog:
                raise RuntimeError("MusicCog not loaded!")

            self.lavalink = music_cog.node  # Access node directly from cog
            logger.debug("[INIT] Lavalink assigned; Lavalink: %s", self.lavalink)
        except Exception as e:
            logger.exception("Unexpected error in voice client init: %s", e)

    async def on_voice_server_update(self, data):
        logger.debug("[VOICE SERVER UPDATE] Received data: %s", data)
        player = self.lavalink.get_player(self.channel.guild.id)
        await player.raw_voice_server_update(data.get("endpoint"), data.get("token"))

    async def on_voice_state_update(self, data):
        logger.debug("[VOICE STATE UPDATE] Received data: %s", data)
        player = self.lavalink.get_player(self.channel.guild.id)
        await player.raw_voice_state_update(
            int(data["user_id"]), data["session_id"], int(data["channel_id"])
        )

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        logger.debug("[CONNECT] Attempting to connect voice client...")
        await self.channel.guild.change_voice_state(
            channel=self.channel, self_mute=self_mute, self_deaf=self_deaf
        )

    async def disconnect(self, *, force: bool = False) -> None:
        logger.debug("[DISCONNECT] Attempting to disconnect voice client...")

        await self.channel.guild.change_voice_state(channel=None)
        self.cleanup()


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
