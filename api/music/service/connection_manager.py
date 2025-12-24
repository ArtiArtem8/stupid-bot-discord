from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import discord
import mafic
from discord.ext import commands

import config
from api.music.models import (
    NodeNotConnectedError,
    VoiceCheckResult,
    VoiceJoinResult,
)
from api.music.player import MusicPlayer, music_player_factory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages Lavalink node connections and Discord voice state."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pool = mafic.NodePool(bot)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize Lavalink node connection."""
        if self._initialized:
            return

        logger.debug("Initializing Mafic node pool")
        try:
            await self.pool.create_node(
                host=config.LAVALINK_HOST,
                port=config.LAVALINK_PORT,
                password=config.LAVALINK_PASSWORD,
                label="MAIN",
                secure=getattr(config, "LAVALINK_SECURE", False),
            )
            self._initialized = True
            logger.info("Mafic node pool initialized successfully")

        except Exception as e:
            logger.exception("Failed to initialize Mafic node")
            raise NodeNotConnectedError(f"Failed to connect: {e}") from e

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Retrieve the music player for a guild."""
        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            return guild.voice_client
        return None

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        logger.debug("Joining channel: %s", channel)

        voice_client = guild.voice_client

        if (
            voice_client
            and isinstance(
                voice_client.channel, (discord.VoiceChannel, discord.StageChannel)
            )
            and voice_client.channel.id == channel.id
        ):
            return VoiceCheckResult.ALREADY_CONNECTED, None

        try:
            if voice_client and isinstance(voice_client, MusicPlayer):
                old_channel = cast(discord.abc.GuildChannel, voice_client.channel)
                await voice_client.move_to(channel)
                return VoiceCheckResult.MOVED_CHANNELS, old_channel

            if not self.pool.nodes:
                await self.initialize()

            await channel.connect(cls=music_player_factory)

            return VoiceCheckResult.SUCCESS, None

        except Exception:
            logger.exception("Failed to join voice channel")
            return VoiceCheckResult.CONNECTION_FAILED, None

    async def disconnect(self, guild: discord.Guild, force: bool = False) -> None:
        """Disconnects the bot from the voice channel."""
        if guild.voice_client:
            logger.debug("Disconnecting from channel: %s", guild.voice_client.channel)
            return await guild.voice_client.disconnect(force=force)
        logger.debug("No voice client to disconnect in guild: %s", guild.id)
