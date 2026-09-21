from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeGuard, cast

import aiohttp
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


class _AvailabilityResult(Enum):
    """Distinguish readiness from retryable and terminal connection failures."""

    READY = auto()
    RETRY_LATER = auto()
    TERMINAL_FAILURE = auto()


def _is_retryable_connect_error(exc: Exception) -> bool:
    # TLS verification and protocol errors share the connection-error base.
    if isinstance(
        exc,
        (
            aiohttp.ClientSSLError,
            aiohttp.ServerFingerprintMismatch,
            aiohttp.TooManyRedirects,
        ),
    ):
        return False
    if isinstance(exc, (mafic.HTTPException, aiohttp.ClientResponseError)):
        return exc.status == 429 or 500 <= exc.status < 600
    return isinstance(
        exc,
        (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, TimeoutError),
    )


@dataclass(frozen=True, slots=True)
class _PlayerStatus:
    is_music_player: bool
    player_stale: bool | None
    guild_present: bool
    is_current_voice_client: bool
    player_connected: bool | None
    node_assigned: bool
    node_label: str | None
    node_in_pool: bool
    node_available: bool | None

    @property
    def is_current(self) -> bool:
        return (
            self.is_music_player
            and self.player_stale is False
            and self.guild_present
            and self.is_current_voice_client
        )

    @property
    def is_usable(self) -> bool:
        return (
            self.is_current
            and self.player_connected is True
            and self.node_assigned
            and self.node_in_pool
            and self.node_available is True
        )


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
        self._join_locks: dict[int, asyncio.Lock] = {}

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

            except (aiohttp.ClientError, TimeoutError, mafic.HTTPException) as exc:
                if not _is_retryable_connect_error(exc):
                    self._initialized = False
                    self._last_connect_error = type(exc).__name__
                    await self._close_failed_node(node)
                    raise
                self._initialized = False
                self._last_connect_error = type(exc).__name__
                self._next_connect_retry_at = (
                    time.monotonic() + config.LAVALINK_CONNECT_RETRY_DELAY
                )
                await self._close_failed_node(node)
                await self._cleanup_unavailable_nodes()
                logger.warning(
                    "Lavalink initialization failed (%s); music unavailable",
                    type(exc).__name__,
                )
                raise NodeNotConnectedError(MUSIC_SERVICE_UNAVAILABLE_MESSAGE) from exc
            except asyncio.CancelledError:
                # add_node registers only after connect, so pool.close cannot own this.
                await self._close_failed_node(node)
                raise
            except Exception:
                await self._close_failed_node(node)
                raise

    async def _close_failed_node(self, node: mafic.Node[commands.Bot]) -> None:
        try:
            await node.close()
        except Exception:
            logger.debug("Failed to close failed Mafic node", exc_info=True)

    def has_ready_node(self) -> bool:
        """Return whether the pool has a usable Lavalink node."""
        return any(getattr(node, "available", False) for node in self.pool.nodes)

    def _pool_contains_node(self, node: mafic.Node[commands.Bot]) -> bool:
        return self.pool.label_to_node.get(node.label) is node

    def is_known_unavailable(self) -> bool:
        """Return whether the last lazy connection attempt failed recently."""
        return bool(self._last_connect_error) and not self.has_ready_node()

    def is_current_player(self, player: object) -> TypeGuard[MusicPlayer]:
        """Return whether this is the current non-stale guild voice client."""
        return self._player_status(player).is_current

    def is_player_usable(self, player: object) -> bool:
        """Return whether a current, connected player uses an available node."""
        return self._player_status(player).is_usable

    def _player_status(
        self,
        player: object,
        *,
        guild_id: int | None = None,
    ) -> _PlayerStatus:
        if not isinstance(player, MusicPlayer):
            guild = self.bot.get_guild(guild_id) if guild_id is not None else None
            return _PlayerStatus(
                is_music_player=False,
                player_stale=None,
                guild_present=guild is not None,
                is_current_voice_client=(
                    player is not None
                    and guild is not None
                    and guild.voice_client is player
                ),
                player_connected=None,
                node_assigned=False,
                node_label=None,
                node_in_pool=False,
                node_available=None,
            )

        guild_id = player.guild.id
        guild = self.bot.get_guild(guild_id)
        node = self.get_player_node(player)
        return _PlayerStatus(
            is_music_player=True,
            player_stale=player.is_stale,
            guild_present=guild is not None,
            is_current_voice_client=(
                guild is not None and guild.voice_client is player
            ),
            player_connected=player.connected,
            node_assigned=node is not None,
            node_label=node.label if node is not None else None,
            node_in_pool=node is not None and node in self.pool.nodes,
            node_available=node.available if node is not None else None,
        )

    def _log_player_state(
        self,
        player: object,
        *,
        guild_id: int,
        summary: str,
        context: str,
        error: str | None = None,
        status: _PlayerStatus | None = None,
    ) -> None:
        status = status or self._player_status(player, guild_id=guild_id)
        message_format = (
            "%s guild=%s context=%s error=%s player_type=%s player_stale=%s "
            + "guild_present=%s is_current_voice_client=%s player_connected=%s "
            + "node_assigned=%s node_label=%s node_in_pool=%s node_available=%s"
        )
        logger.warning(
            message_format,
            summary,
            guild_id,
            context,
            error,
            type(player).__name__ if player is not None else None,
            status.player_stale,
            status.guild_present,
            status.is_current_voice_client,
            status.player_connected,
            status.node_assigned,
            status.node_label,
            status.node_in_pool,
            status.node_available,
        )

    def get_player_node(self, player: MusicPlayer) -> mafic.Node[commands.Bot] | None:
        """Return the player's assigned node without selecting a fallback."""
        return cast("mafic.Node[commands.Bot] | None", player.assigned_node)

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

    async def invalidate_player(
        self,
        player: MusicPlayer,
        *,
        context: str | None = None,
        error: str | None = None,
    ) -> None:
        """Mark a failed player stale and detach its local voice client."""
        if context is not None:
            self._log_player_state(
                player,
                guild_id=player.guild.id,
                summary="Player invalidation",
                context=context,
                error=error,
            )
        player.mark_stale()
        await self.detach_stale_voice_client(player.guild, player)

    def _snapshot_node_players(
        self, player: MusicPlayer
    ) -> tuple[mafic.Node[commands.Bot] | None, list[MusicPlayer]]:
        node = self.get_player_node(player)
        if node is None:
            return node, [player]

        players: list[MusicPlayer] = [
            candidate
            for candidate in node.players
            if isinstance(candidate, MusicPlayer)
        ]
        if not any(candidate is player for candidate in players):
            players.append(player)
        return node, players

    async def invalidate_node_and_players(self, player: MusicPlayer) -> None:
        """Invalidate the player's node, then detach all of its players locally."""
        node, players = self._snapshot_node_players(player)
        for candidate in players:
            candidate.mark_stale()
        try:
            await self.mark_node_unavailable(node)
        finally:
            for candidate in players:
                await self._cleanup_voice_client_locally(
                    candidate.guild,
                    candidate,
                )

    async def handle_node_unavailable(self, node: mafic.Node[commands.Bot]) -> set[int]:
        """Transfer players to a ready node or invalidate only failed guilds."""
        players = [
            player for player in tuple(node.players) if isinstance(player, MusicPlayer)
        ]
        target = next(
            (
                candidate
                for candidate in self.pool.nodes
                if candidate is not node and candidate.available
            ),
            None,
        )
        invalidated: list[MusicPlayer] = []

        for player in players:
            if target is not None:
                try:
                    await player.transfer_to(target)
                except (*EXPECTED_LAVALINK_IO_ERRORS, RuntimeError) as exc:
                    self._log_player_state(
                        player,
                        guild_id=player.guild.id,
                        summary="Player transfer failed",
                        context="node_unavailable_transfer",
                        error=type(exc).__name__,
                    )
                else:
                    if self.is_player_usable(player):
                        continue
            player.mark_stale()
            invalidated.append(player)

        try:
            await self.mark_node_unavailable(node)
        finally:
            for player in invalidated:
                await self._cleanup_voice_client_locally(player.guild, player)

        return {player.guild.id for player in invalidated}

    async def ensure_available(self) -> bool:
        """Ensure Lavalink is available for a command-facing operation."""
        return await self._check_availability() is _AvailabilityResult.READY

    async def _check_availability(self) -> _AvailabilityResult:
        if self.has_ready_node():
            return _AvailabilityResult.READY

        now = time.monotonic()
        if now < self._next_connect_retry_at:
            message_format = (
                "Lavalink connection retry cooldown active "
                + "last_connect_error=%s retry_in_seconds=%.1f"
            )
            logger.debug(
                message_format,
                self._last_connect_error,
                self._next_connect_retry_at - now,
            )
            return _AvailabilityResult.RETRY_LATER

        try:
            await self.initialize()
        except NodeNotConnectedError:
            return _AvailabilityResult.RETRY_LATER
        except (aiohttp.ClientError, TimeoutError, mafic.HTTPException) as exc:
            # initialize converts retryable external failures to NodeNotConnectedError.
            logger.warning(
                "Lavalink unavailable due to terminal connection failure (%s)",
                type(exc).__name__,
            )
            return _AvailabilityResult.TERMINAL_FAILURE

        return (
            _AvailabilityResult.READY
            if self.has_ready_node()
            else _AvailabilityResult.RETRY_LATER
        )

    def start_lazy_connect(self) -> None:
        """Start at most one bootstrap task, ending when Lavalink becomes ready."""
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
            while True:
                result = await self._check_availability()
                if result is _AvailabilityResult.READY:
                    return
                if result is _AvailabilityResult.TERMINAL_FAILURE:
                    return
                retry_in = self._next_connect_retry_at - time.monotonic()
                await asyncio.sleep(
                    retry_in if retry_in > 0 else config.LAVALINK_CONNECT_RETRY_DELAY
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected lazy Lavalink connection failure")

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
            if self._pool_contains_node(node):
                await self.pool.remove_node(node, transfer_players=False)
                return
        except Exception:
            logger.debug("Failed to remove unavailable Mafic node", exc_info=True)

        await self._close_failed_node(node)

    async def _cleanup_unavailable_nodes(self) -> None:
        """Close unusable Mafic nodes left behind by a failed connection attempt."""
        nodes = list(self.pool.label_to_node.values())
        for node in nodes:
            if getattr(node, "available", False):
                continue
            try:
                await self.pool.remove_node(node, transfer_players=False)
            except Exception:
                logger.debug("Failed to cleanup unavailable Mafic node", exc_info=True)

    def get_player(
        self,
        guild_id: int,
        *,
        failure_context: str | None = None,
    ) -> MusicPlayer | None:
        """Retrieve the music player for a guild.

        Returns None if the guild is not connected or does not have a music player.

        Returns:
            MusicPlayer | None

        """
        guild = self.bot.get_guild(guild_id)
        voice_client = guild.voice_client if guild is not None else None
        if isinstance(voice_client, MusicPlayer) and self.is_player_usable(
            voice_client
        ):
            return voice_client
        if failure_context is not None:
            status = self._player_status(voice_client, guild_id=guild_id)
            self._log_player_state(
                voice_client,
                guild_id=guild_id,
                summary="Player unusable",
                context=failure_context,
                status=status,
            )
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
        lock = self._join_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            return await self._join_unlocked(guild, channel)

    async def _connect_new_player(
        self, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        player = await channel.connect(cls=music_player_factory, timeout=8.0)
        if not self.is_player_usable(player):
            status = self._player_status(player)
            self._log_player_state(
                player,
                guild_id=player.guild.id,
                summary="Player unusable",
                context="fresh_connect_validation",
                status=status,
            )
            await self.invalidate_player(player)
            return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
        return VoiceCheckResult.SUCCESS, None

    async def _join_unlocked(
        self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel
    ) -> VoiceJoinResult:
        """Join a voice channel while holding this guild's join lock."""
        logger.debug("Joining channel: %s", channel)

        try:
            existing_result = await self._handle_existing_voice_client(
                guild, channel, guild.voice_client
            )
            if existing_result is not None:
                return existing_result

            if not await self.ensure_available():
                logger.info(
                    "Node unavailable for music join guild=%s last_connect_error=%s",
                    guild.id,
                    self._last_connect_error,
                )
                return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
            return await self._connect_new_player(channel)

        except TimeoutError:
            logger.warning("Timeout while joining voice channel")
            await self._detach_voice_client_after_failed_connect(guild)
            return VoiceCheckResult.TIMEOUT, None
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            await self._handle_join_io_failure(guild, exc)
            return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
        except discord.ClientException as exc:
            logger.warning(
                "Discord voice connection failed for guild %s with %s",
                guild.id,
                type(exc).__name__,
            )
            await self._detach_voice_client_after_failed_connect(guild)
            return VoiceCheckResult.CONNECTION_FAILED, None

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
            if voice_client or not await self.ensure_available():
                return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None

        if self._is_connected_to_channel(voice_client, channel):
            return VoiceCheckResult.ALREADY_CONNECTED, None

        if isinstance(voice_client, MusicPlayer):
            return await self._reuse_or_move_player(voice_client, channel)
        return None

    async def _detach_unusable_player(
        self, guild: discord.Guild, player: MusicPlayer
    ) -> None:
        status = self._player_status(player)
        self._log_player_state(
            player,
            guild_id=guild.id,
            summary="Player unusable",
            context="existing_voice_client_validation",
            status=status,
        )
        node = self.get_player_node(player)
        if (
            status.is_current
            and node is not None
            and self._pool_contains_node(node)
            and not node.available
        ):
            await self.invalidate_node_and_players(player)
        else:
            await self.invalidate_player(player)
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
        player: MusicPlayer,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> VoiceJoinResult:
        old_channel = cast(discord.abc.GuildChannel, cast(object, player.channel))
        if not self.is_player_usable(player):
            status = self._player_status(player)
            self._log_player_state(
                player,
                guild_id=player.guild.id,
                summary="Player unusable",
                context="pre_move_validation",
                status=status,
            )
            await self.invalidate_player(player)
            return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
        try:
            await player.move_to(channel, timeout=5.0)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            await self.invalidate_player(
                player,
                context="voice_move_io_failure",
                error=type(exc).__name__,
            )
            return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
        if not self.is_player_usable(player):
            status = self._player_status(player)
            self._log_player_state(
                player,
                guild_id=player.guild.id,
                summary="Player unusable",
                context="post_move_validation",
                status=status,
            )
            await self.invalidate_player(player)
            return VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE, None
        return VoiceCheckResult.MOVED_CHANNELS, old_channel

    async def _handle_join_io_failure(
        self, guild: discord.Guild, exc: Exception
    ) -> None:
        if isinstance(guild.voice_client, MusicPlayer):
            await self.invalidate_player(
                guild.voice_client,
                context="voice_join_io_failure",
                error=type(exc).__name__,
            )
            return
        self._log_player_state(
            guild.voice_client,
            guild_id=guild.id,
            summary="Player invalidation",
            context="voice_join_io_failure",
            error=type(exc).__name__,
        )
        await self._detach_voice_client_after_failed_connect(guild)

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
        """Best-effort stale voice cleanup that never exposes Lavalink IO errors."""
        if isinstance(voice_client, MusicPlayer):
            voice_client.mark_stale()

        if guild.voice_client is not voice_client:
            logger.debug("Skipping disconnect for non-current voice client")
            return

        try:
            await voice_client.disconnect(force=True)
        except EXPECTED_LAVALINK_IO_ERRORS:
            logger.debug("Ignoring remote disconnect failure for stale music player")
        except Exception:
            logger.debug("Failed to disconnect stale music player", exc_info=True)

        await self._cleanup_voice_client_locally(guild, voice_client)

    async def _cleanup_voice_client_locally(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceProtocol,
    ) -> None:
        """Best-effort local cleanup without another remote disconnect."""
        if guild.voice_client is not voice_client:
            logger.debug("Skipping local cleanup for non-current voice client")
            return

        try:
            await guild.change_voice_state(channel=None)
        except Exception:
            logger.debug("Failed to clear guild voice state locally", exc_info=True)

        if guild.voice_client is not voice_client:
            logger.debug("Skipping cache cleanup for non-current voice client")
            return

        try:
            await maybe_coroutine(voice_client.cleanup)
        except Exception:
            logger.debug("Failed to cleanup stale voice client locally", exc_info=True)

    async def disconnect(self, guild: discord.Guild, force: bool = False) -> bool:
        """Disconnect from voice and report whether the guild has no voice client."""
        voice_client = guild.voice_client
        if not voice_client:
            logger.debug("No voice client to disconnect in guild: %s", guild.id)
            return True

        logger.debug(
            "Disconnecting from channel: %s",
            getattr(voice_client, "channel", None),
        )

        if isinstance(voice_client, MusicPlayer) and not self.is_player_usable(
            voice_client
        ):
            await self._detach_unusable_player(guild, voice_client)
            return guild.voice_client is None

        try:
            await voice_client.disconnect(force=force)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            if isinstance(voice_client, MusicPlayer):
                await self.invalidate_player(
                    voice_client,
                    context="voice_disconnect_io_failure",
                    error=type(exc).__name__,
                )
            else:
                logger.warning(
                    "Voice disconnect failed with expected IO error: %s",
                    type(exc).__name__,
                )
                await self.detach_stale_voice_client(guild, voice_client)
            return guild.voice_client is None
        except Exception:
            logger.debug("Unexpected voice disconnect failure", exc_info=True)
            await self.detach_stale_voice_client(guild, voice_client)
            return guild.voice_client is None

        # Even a successful disconnect can leave a VoiceProtocol cached in edge cases.
        if guild.voice_client is voice_client:
            try:
                await maybe_coroutine(voice_client.cleanup)
            except Exception:
                logger.exception(
                    "Failed to cleanup disconnected voice client for guild %s",
                    guild.id,
                )
        return guild.voice_client is None
