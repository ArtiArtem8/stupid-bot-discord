from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import cast

import discord
import mafic
from discord.ext import commands
from discord.utils import maybe_coroutine

import config
from api.music.errors import EXPECTED_LAVALINK_IO_ERRORS
from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    NodeNotConnectedError,
    VoiceCheckResult,
    VoiceJoinResult,
)
from api.music.player import MusicPlayer, music_player_factory

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages Lavalink node connections and Discord voice state."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pool = mafic.NodePool(bot)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._next_connect_retry_at = 0.0
        self._last_connect_error: str | None = None
        self._lazy_connect_task: asyncio.Task[None] | None = None
        self._stale_player_ids: set[int] = set()

    async def initialize(self) -> None:
        """Initialize Lavalink node connection."""
        if self.has_ready_node():
            self._initialized = True
            return

        async with self._init_lock:
            if self.has_ready_node():
                self._initialized = True
                return

            await self._cleanup_unavailable_nodes()

            logger.debug("Initializing Mafic node pool")
            node = mafic.Node(
                host=config.LAVALINK_HOST,
                port=config.LAVALINK_PORT,
                password=config.LAVALINK_PASSWORD,
                label=config.LAVALINK_NODE_LABEL,
                client=self.bot,
                secure=config.LAVALINK_SECURE,
            )
            try:
                player_cls = cast("type[mafic.Player[commands.Bot]]", MusicPlayer)
                await self.pool.add_node(node, player_cls=player_cls)
                self._initialized = True
                self._last_connect_error = None
                self._next_connect_retry_at = 0.0
                logger.info("Mafic node pool initialized successfully")

            except Exception as exc:
                self._initialized = False
                self._last_connect_error = str(exc)
                self._next_connect_retry_at = (
                    time.monotonic() + config.LAVALINK_CONNECT_RETRY_DELAY
                )
                await self._close_failed_node(node)
                await self._cleanup_unavailable_nodes()
                logger.warning(
                    "Lavalink node is unavailable; music commands will fail softly."
                )
                logger.debug("Failed to initialize Mafic node", exc_info=True)
                raise NodeNotConnectedError(MUSIC_SERVICE_UNAVAILABLE_MESSAGE) from exc

    async def _close_failed_node(self, node: mafic.Node[commands.Bot]) -> None:
        try:
            await node.close()
        except Exception:
            logger.debug("Failed to close failed Mafic node", exc_info=True)

    def has_ready_node(self) -> bool:
        """Return whether the pool has a usable Lavalink node."""
        return any(getattr(node, "available", False) for node in self.pool.nodes)

    def is_known_unavailable(self) -> bool:
        """Return whether the last lazy connection attempt failed recently."""
        return bool(self._last_connect_error) and not self.has_ready_node()

    def is_player_usable(self, player: object) -> bool:
        """Return whether a player is still bound to a live node in this pool."""
        if not isinstance(player, MusicPlayer):
            return False

        if id(player) in self._stale_player_ids:
            return False

        node = self.get_player_node(player)
        if node is None:
            return False
        if node not in self.pool.nodes:
            return False
        return node.available

    def get_player_node(self, player: MusicPlayer) -> mafic.Node[commands.Bot] | None:
        return cast("mafic.Node[commands.Bot] | None", getattr(player, "_node", None))

    async def mark_node_unavailable(
        self, node: mafic.Node[commands.Bot] | None = None
    ) -> None:
        """Mark Lavalink unavailable and remove stale node resources."""
        async with self._init_lock:
            self._initialized = False
            self._last_connect_error = "Lavalink node became unavailable"
            self._next_connect_retry_at = (
                time.monotonic() + config.LAVALINK_CONNECT_RETRY_DELAY
            )

            if node is not None:
                await self._remove_or_close_node(node)

            await self._cleanup_unavailable_nodes()

    async def ensure_available(self) -> bool:
        """Lazily connect to Lavalink, respecting the retry cooldown."""
        if self.has_ready_node():
            return True

        if time.monotonic() < self._next_connect_retry_at:
            return False

        try:
            await self.initialize()
        except NodeNotConnectedError:
            return False

        return self.has_ready_node()

    def start_lazy_connect(self) -> None:
        """Schedule a background Lavalink connection attempt."""
        if self.has_ready_node():
            return

        if self._lazy_connect_task and not self._lazy_connect_task.done():
            return

        self._lazy_connect_task = asyncio.create_task(
            self._run_lazy_connect(),
            name="music-lavalink-lazy-connect",
        )

    async def _run_lazy_connect(self) -> None:
        try:
            await self.ensure_available()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Unexpected lazy Lavalink connection failure", exc_info=True)

    async def cleanup(self) -> None:
        """Cancel pending connection work and close Mafic resources."""
        task = self._lazy_connect_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._lazy_connect_task = None

        await self.pool.close()
        self._initialized = False

    async def _remove_or_close_node(self, node: mafic.Node[commands.Bot]) -> None:
        try:
            if node in self.pool.nodes:
                await self.pool.remove_node(node, transfer_players=False)
                return
        except Exception:
            logger.debug("Failed to remove unavailable Mafic node", exc_info=True)

        await self._close_failed_node(node)

    async def _cleanup_unavailable_nodes(self) -> None:
        """Close unusable Mafic nodes left behind by a failed connection attempt."""
        nodes = list(self.pool.nodes)
        for node in nodes:
            if getattr(node, "available", False):
                continue
            try:
                await self.pool.remove_node(node, transfer_players=False)
            except Exception:
                logger.debug("Failed to cleanup unavailable Mafic node", exc_info=True)

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Retrieve the music player for a guild.

        Returns None if the guild is not connected or does not have a music player.

        Returns:
            MusicPlayer | None

        """
        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            if not self.is_player_usable(guild.voice_client):
                return None
            return guild.voice_client
        return None

    async def _detach_voice_client_after_failed_connect(
        self, guild: discord.Guild
    ) -> None:
        voice_client = guild.voice_client
        if not voice_client:
            await asyncio.sleep(0.01)
            voice_client = guild.voice_client
        if not voice_client:
            return
        await self.detach_stale_voice_client(guild, voice_client)

    async def join(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        logger.debug("Joining channel: %s", channel)

        try:
            voice_client = self._get_existing_voice_client(guild)
            existing_result = await self._handle_existing_voice_client(
                guild, channel, voice_client
            )
            if existing_result is not None:
                return existing_result

            if not await self._ensure_join_available():
                return self._voice_join_failure(
                    VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE
                )
            return await self._connect_new_player(channel)

        except TimeoutError:
            logger.warning("Timeout while joining voice channel")
            await self._detach_voice_client_after_failed_connect(guild)
            return self._voice_join_failure(VoiceCheckResult.TIMEOUT)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            await self._handle_join_io_failure(guild, exc)
            return self._voice_join_failure(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        except Exception:
            logger.exception("Failed to join voice channel")
            await self._detach_voice_client_after_failed_connect(guild)
            return self._voice_join_failure(VoiceCheckResult.CONNECTION_FAILED)

    async def _ensure_join_available(self) -> bool:
        return await self.ensure_available()

    def _get_existing_voice_client(
        self, guild: discord.Guild
    ) -> discord.VoiceProtocol | None:
        return guild.voice_client

    async def _handle_existing_voice_client(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
        voice_client: discord.VoiceProtocol | None,
    ) -> VoiceJoinResult | None:
        if isinstance(voice_client, MusicPlayer) and not self.is_player_usable(
            voice_client
        ):
            await self._detach_unusable_player(guild, voice_client)
            voice_client = guild.voice_client
            if voice_client or not await self._ensure_join_available():
                return self._voice_join_failure(
                    VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE
                )

        if self._is_connected_to_channel(voice_client, channel):
            return self._voice_join_success(VoiceCheckResult.ALREADY_CONNECTED)

        if isinstance(voice_client, MusicPlayer):
            return await self._reuse_or_move_player(guild, voice_client, channel)
        return None

    async def _detach_unusable_player(
        self, guild: discord.Guild, player: MusicPlayer
    ) -> None:
        node = self.get_player_node(player)
        if node is not None and node in self.pool.nodes:
            await self.mark_node_unavailable(node)
        await self.detach_stale_voice_client(guild, player)
        logger.debug(
            "Detached stale voice client for guild %s; remaining voice_client=%r",
            guild.id,
            guild.voice_client,
        )

    def _is_connected_to_channel(
        self,
        voice_client: discord.VoiceProtocol | None,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        return bool(
            voice_client
            and isinstance(
                voice_client.channel, (discord.VoiceChannel, discord.StageChannel)
            )
            and voice_client.channel.id == channel.id
        )

    async def _reuse_or_move_player(
        self,
        guild: discord.Guild,
        player: MusicPlayer,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> VoiceJoinResult:
        old_channel = cast(discord.abc.GuildChannel, cast(object, player.channel))
        if not self.is_player_usable(player):
            await self.detach_stale_voice_client(guild, player)
            return self._voice_join_failure(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        try:
            await player.move_to(channel, timeout=5.0)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            await self._handle_voice_client_io_failure(guild, player, exc)
            return self._voice_join_failure(VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE)
        return self._voice_join_success(VoiceCheckResult.MOVED_CHANNELS, old_channel)

    async def _connect_new_player(
        self, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        await channel.connect(cls=music_player_factory, timeout=8.0)
        return self._voice_join_success(VoiceCheckResult.SUCCESS)

    def _voice_join_success(
        self,
        status: VoiceCheckResult,
        old_channel: discord.abc.GuildChannel | None = None,
    ) -> VoiceJoinResult:
        return status, old_channel

    def _voice_join_failure(
        self,
        status: VoiceCheckResult,
        old_channel: discord.abc.GuildChannel | None = None,
    ) -> VoiceJoinResult:
        return status, old_channel

    async def _handle_join_io_failure(
        self, guild: discord.Guild, exc: Exception
    ) -> None:
        logger.warning(
            "Lavalink IO failure while joining voice: %s", type(exc).__name__
        )
        if isinstance(guild.voice_client, MusicPlayer):
            await self._handle_voice_client_io_failure(guild, guild.voice_client, exc)
            return
        await self._detach_voice_client_after_failed_connect(guild)
        await self.mark_node_unavailable()

    async def _handle_voice_client_io_failure(
        self, guild: discord.Guild, voice_client: MusicPlayer, exc: Exception
    ) -> None:
        logger.warning("Lavalink voice client failure: %s", type(exc).__name__)
        node = self.get_player_node(voice_client)
        if node is not None:
            await self.mark_node_unavailable(node)
        else:
            await self.mark_node_unavailable()
        await self.detach_stale_voice_client(guild, voice_client)

    def _bot_voice_channel(
        self, guild: discord.Guild
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        me = guild.me
        voice = getattr(me, "voice", None)
        channel = getattr(voice, "channel", None)
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return channel
        return None

    async def detach_stale_voice_client(
        self, guild: discord.Guild, voice_client: discord.VoiceProtocol
    ) -> None:
        """Best-effort local voice cleanup that never exposes Lavalink IO errors."""
        if isinstance(voice_client, MusicPlayer):
            self._stale_player_ids.add(id(voice_client))

        try:
            await voice_client.disconnect(force=True)
        except EXPECTED_LAVALINK_IO_ERRORS:
            logger.debug("Ignoring remote disconnect failure for stale music player")
        except Exception:
            logger.debug("Failed to disconnect stale music player", exc_info=True)

        try:
            await guild.change_voice_state(channel=None)
        except Exception:
            logger.debug("Failed to clear guild voice state locally", exc_info=True)

        try:
            await maybe_coroutine(voice_client.cleanup)
        except Exception:
            logger.debug("Failed to cleanup stale voice client locally", exc_info=True)

    async def disconnect(self, guild: discord.Guild, force: bool = False) -> None:
        """Disconnect the bot from voice, including stale/dead music clients."""
        voice_client = guild.voice_client
        if not voice_client:
            logger.debug("No voice client to disconnect in guild: %s", guild.id)
            return

        logger.debug(
            "Disconnecting from channel: %s",
            getattr(voice_client, "channel", None),
        )

        if isinstance(voice_client, MusicPlayer) and not self.is_player_usable(
            voice_client
        ):
            node = self.get_player_node(voice_client)
            if node is not None and node in self.pool.nodes:
                await self.mark_node_unavailable(node)
            await self.detach_stale_voice_client(guild, voice_client)
            return

        try:
            await voice_client.disconnect(force=force)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            if isinstance(voice_client, MusicPlayer):
                await self._handle_voice_client_io_failure(guild, voice_client, exc)
            else:
                logger.warning(
                    "Voice disconnect failed with expected IO error: %s",
                    type(exc).__name__,
                )
                await self.detach_stale_voice_client(guild, voice_client)
            return
        except Exception:
            logger.debug("Unexpected voice disconnect failure", exc_info=True)
            await self.detach_stale_voice_client(guild, voice_client)
            return

        # Even a successful disconnect can leave a VoiceProtocol cached in edge cases.
        with contextlib.suppress(Exception):
            await maybe_coroutine(voice_client.cleanup)
