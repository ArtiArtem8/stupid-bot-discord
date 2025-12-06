"""Music API for Lavalink integration."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from typing import Any, Literal, TypedDict, cast, override

import discord
import lavaplay  # type: ignore
from discord.channel import VocalGuildChannel
from discord.ext import commands, tasks
from discord.utils import utcnow
from lavaplay.events import *  # type: ignore

# from lavaplay.events import (  # type: ignore
#     TrackEndEvent,
#     TrackStartEvent,
#     WebSocketClosedEvent,
# )
from lavaplay.player import Player  # type: ignore

import config
from utils.json_utils import get_json, save_json

LOGGER = logging.getLogger(__name__)

type Track = lavaplay.Track
type PlayList = lavaplay.PlayList


class MusicError(Exception):
    """Base exception for Music API errors."""

    pass


class NodeNotConnectedError(MusicError):
    """Raised when Lavalink node is not connected."""

    pass


class PlaylistResponseData(TypedDict):
    type: Literal["playlist"]
    playlist: PlayList


class TrackResponseData(TypedDict):
    type: Literal["track"]
    track: Track
    playing: bool


type PlayResponseData = PlaylistResponseData | TrackResponseData


class SkipTrackData(TypedDict):
    before: Track | None
    after: Track | None


class RepeatModeData(TypedDict):
    mode: str
    previous: str


class RotateTrackData(TypedDict):
    skipped: Track | None
    next: Track | None


class MusicResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ERROR = "ERROR"


class RepeatMode(StrEnum):
    OFF = "off"
    QUEUE = "queue"


class VoiceCheckResult(StrEnum):
    ALREADY_CONNECTED = auto()
    CHANNEL_EMPTY = auto()
    CONNECTION_FAILED = auto()
    INVALID_CHANNEL_TYPE = auto()
    MOVED_CHANNELS = auto()
    SUCCESS = auto()
    USER_NOT_IN_VOICE = auto()
    USER_NOT_MEMBER = auto()

    @property
    def status(self) -> MusicResultStatus:
        match self:
            case self.ALREADY_CONNECTED | self.MOVED_CHANNELS | self.SUCCESS:
                return MusicResultStatus.SUCCESS
            case self.CONNECTION_FAILED:
                return MusicResultStatus.ERROR
            case _:
                return MusicResultStatus.FAILURE


type VoiceJoinResult = tuple[VoiceCheckResult, VocalGuildChannel | None]
# named tuple - perhaps?


@dataclass(slots=True)
class TrackInfo:
    title: str
    uri: str
    skipped: bool = False
    requester_id: str | None = None


@dataclass
class MusicSession:
    guild_id: int
    start_time: datetime = field(default_factory=utcnow)
    tracks: list[TrackInfo] = field(default_factory=list[TrackInfo])
    channel_usage: dict[int, int] = field(default_factory=dict[int, int])
    participants: set[int] = field(default_factory=set[int])

    def record_interaction(self, channel_id: int, user_id: int) -> None:
        self.channel_usage[channel_id] = self.channel_usage.get(channel_id, 0) + 1
        self.participants.add(user_id)


@dataclass(frozen=True, slots=True)
class MusicResult[T]:
    """Result of a music operation."""

    status: MusicResultStatus
    message: str
    data: T | None = None

    @property
    def is_success(self) -> bool:
        return self.status is MusicResultStatus.SUCCESS


class LavalinkVoiceClient(discord.VoiceClient):
    """A voice client for Lavalink."""

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        LOGGER.debug("[INIT] Creating voice client...")
        super().__init__(client, channel)
        try:
            cog = self.client.get_cog("MusicCog")  # type: ignore TODO: Review
            if not cog:
                raise RuntimeError("MusicCog not loaded!")
            self.lavalink = getattr(cast(Any, cog), "node", None)
            self.lavalink = cast(lavaplay.Node, self.lavalink)
            LOGGER.debug("[INIT] Lavalink assigned; Lavalink: %s", self.lavalink)
        except Exception as e:
            LOGGER.exception("Unexpected error in voice client init: %s", e)
            self.lavalink = None

    def _get_player(self, lavalink: lavaplay.Node) -> None | Player:
        return cast(
            None | Player,
            lavalink.get_player(self.channel.guild.id),
        )

    @override
    async def on_voice_server_update(self, data: dict[str, str]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        LOGGER.debug("[VOICE SERVER UPDATE] Received data: %s", data)
        if self.lavalink is None:
            LOGGER.critical("Lavalink is not initialized!")
            return

        player = self._get_player(self.lavalink)
        if player is None:
            LOGGER.critical("Player is not initialized!")
            return

        endpoint = data.get("endpoint", "missing")
        token = data.get("token", "missing")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await player.raw_voice_server_update(endpoint, token)
                LOGGER.debug(
                    "[VOICE SERVER UPDATE] Successfully updated (attempt %d/%d)",
                    attempt + 1,
                    max_retries,
                )
                break
            except Exception as e:
                LOGGER.warning(
                    "[VOICE SERVER UPDATE] Failed attempt %d/%d: %s",
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    LOGGER.error(
                        "[VOICE SERVER UPDATE] All retry attempts failed for endpoint %s",
                        endpoint,
                    )

    @override
    async def on_voice_state_update(self, data: dict[str, Any]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        LOGGER.debug("[VOICE STATE UPDATE] Received data: %s", data)
        if self.lavalink is None:
            return

        player = self._get_player(self.lavalink)
        channel_id = cast(str | int | None, data.get("channel_id"))

        if player is None:
            LOGGER.critical("Player is not initialized!")
            return

        if channel_id is None:
            LOGGER.debug("[VOICE STATE UPDATE] Disconnected from VC: channel_id = None")
            try:
                await player.raw_voice_state_update(
                    int(data["user_id"]),
                    data["session_id"],
                    None,
                )
                await self.disconnect()
            except Exception as e:
                LOGGER.error("[VOICE STATE UPDATE] Failed to notify disconnect: %s", e)
            return

        channel_id = int(channel_id)
        if self.channel and channel_id != self.channel.id:
            channel = self.client.get_channel(channel_id)
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                LOGGER.debug(
                    "[VOICE STATE UPDATE] Bot moved from %s to %s",
                    self.channel.name,
                    channel.name,
                )
                self.channel = channel
        try:
            await player.raw_voice_state_update(
                int(data["user_id"]),
                data["session_id"],
                channel_id,
            )
        except Exception as e:
            LOGGER.error("[VOICE STATE UPDATE] Failed to update player: %s", e)

    @override
    async def move_to(
        self, channel: discord.abc.Snowflake | None, *, timeout: float | None = 30
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
        LOGGER.debug("[CONNECT] Attempting to connect to %s...", self.channel)
        await self.channel.guild.change_voice_state(
            channel=self.channel, self_mute=self_mute, self_deaf=self_deaf
        )

    @override
    async def disconnect(self, *, force: bool = False) -> None:
        LOGGER.debug("[DISCONNECT] Attempting to disconnect voice client...")

        player = self._get_player(self.lavalink) if self.lavalink else None
        await self.channel.guild.change_voice_state(channel=None)
        self.cleanup()
        if player and force:
            LOGGER.info("[DISCONNECT] Force disconnect - stopping player")
            try:
                await player.stop()
            except Exception as e:
                LOGGER.warning(f"[DISCONNECT] Failed to stop player: {e}")


class MusicAPI:
    """API for managing music playback via Lavalink."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lavalink = lavaplay.Lavalink()
        self.node: lavaplay.Node | None = None
        self.sessions: dict[int, MusicSession] = {}
        self._track_start_times: dict[int, datetime] = {}
        self._last_node_status: dict = {}

    async def initialize(self) -> None:
        """Initialize Lavalink node."""
        if self.node and self.node.is_connect:
            return
        await self._connect_node()
        if self.node is None:
            raise NodeNotConnectedError("Node is None after initialization")

        if not self.node_health_monitor.is_running():
            self.node_health_monitor.start()
        event_types: list[Event] = [
            ReadyEvent,
            StatsUpdateEvent,
            TrackStartEvent,
            TrackEndEvent,
            TrackExceptionEvent,
            TrackStuckEvent,
            WebSocketClosedEvent,
            PlayerUpdateEvent,
            ErrorEvent,
        ]

        for event_type in event_types:
            self.node.event_manager.add_listener(
                event_type.__name__, self._create_debug_handler(event_type.__name__)
            )

        self.node.event_manager.add_listener(
            TrackStartEvent.__name__, self.handle_track_start
        )
        self.node.event_manager.add_listener(
            TrackEndEvent.__name__, self.handle_track_end
        )
        try:
            self.node.event_manager.add_listener(
                WebSocketClosedEvent.__name__,
                self.handle_websocket_closed,
            )
        except ImportError:
            LOGGER.warning("WebSocketClosedEvent not available in lavaplay version")

    @tasks.loop(seconds=60.0)
    async def node_health_monitor(self):
        """Monitor Lavalink node health and status."""
        if self.node is None:
            LOGGER.warning("[HEALTH] Node is None - not initialized")
            return
        self.node.players
        try:
            # Основной статус подключения
            is_connected = self.node.is_connect
            LOGGER.info(f"[HEALTH] Node connected: {is_connected}")

            if not is_connected:
                LOGGER.error("[HEALTH] ❌ Node is NOT connected - attempting reconnect")
                return

            node_info = {
                "host": self.node.host,
                "port": self.node.port,
                "secure": getattr(self.node, "secure", False),
                "connected": is_connected,
                "session_id": getattr(self.node, "session_id", "N/A"),
            }
            # Логируем изменения статуса
            if node_info != self._last_node_status:
                LOGGER.info("[HEALTH] ✅ Node status changed:")
                for key, value in node_info.items():
                    LOGGER.info(f"  - {key}: {value}")
                self._last_node_status = node_info.copy()

            # Проверка готовности к воспроизведению
            ready_to_play = is_connected and self.node.session_id is not None
            if ready_to_play:
                LOGGER.info("[HEALTH] ✅ Ready to play music")
            else:
                LOGGER.warning(
                    f"[HEALTH] ⚠️ Not ready to play - "
                    f"Connected: {is_connected}, "
                    f"Session ID: {getattr(self.node, 'session_id', None)}"
                )

            await self._log_players_info()

        except Exception as e:
            LOGGER.error(f"[HEALTH] Error during health check: {e}", exc_info=True)

    async def _log_players_info(self):
        """Log detailed information about all players in the node."""
        if not hasattr(self.node, "players") or not self.node.players:
            LOGGER.info("[PLAYERS] No active players")
            return

        players_count = len(self.node.players)
        LOGGER.info(f"[PLAYERS] Total active players: {players_count}")
        LOGGER.info("=" * 60)

        for guild_id, player in self.node.players.items():
            try:
                await self._log_single_player(guild_id, player)
            except Exception as e:
                LOGGER.error(
                    f"[PLAYERS] Error logging player {guild_id}: {e}", exc_info=True
                )

        LOGGER.info("=" * 60)

    async def _log_single_player(self, guild_id: int, player):
        """Log detailed information about a single player."""
        LOGGER.info(f"[PLAYER:{guild_id}] Guild ID: {guild_id}")

        # === ОСНОВНАЯ ИНФОРМАЦИЯ ===
        basic_info = {
            "user_id": getattr(player, "user_id", "N/A"),
            "is_connected": getattr(player, "_is_connected", False),
            "is_playing": player.is_playing if hasattr(player, "is_playing") else False,
            "ping": getattr(player, "_ping", 0),
        }

        LOGGER.info(f"[PLAYER:{guild_id}] Basic Info:")
        for key, value in basic_info.items():
            LOGGER.info(f"  - {key}: {value}")

        # === VOICE STATE ===
        voice_state = getattr(player, "_voice_state", None)
        LOGGER.info(f"[PLAYER:{guild_id}] Voice State: {voice_state}")

        # === VOICE HANDLERS ===
        voice_handlers = getattr(player, "_voice_handlers", {})
        if voice_handlers:
            LOGGER.info(f"[PLAYER:{guild_id}] Voice Handlers ({len(voice_handlers)}):")
            for handler_guild_id, conn_info in voice_handlers.items():
                LOGGER.info(
                    f"  - Guild {handler_guild_id}: "
                    f"Session={getattr(conn_info, 'session_id', 'N/A')}, "
                    f"Channel={getattr(conn_info, 'channel_id', 'N/A')}"
                )
        else:
            LOGGER.info(f"[PLAYER:{guild_id}] Voice Handlers: None")

        # === VOICE INFO ===
        voice_info = getattr(player, "_voice_info", {})
        if voice_info:
            LOGGER.info(f"[PLAYER:{guild_id}] Voice Info ({len(voice_info)}):")
            for info_guild_id, v_info in voice_info.items():
                LOGGER.info(
                    f"  - Guild {info_guild_id}: "
                    f"Token={getattr(v_info, 'token', 'N/A')[:20]}..., "
                    f"Endpoint={getattr(v_info, 'endpoint', 'N/A')}"
                )
        else:
            LOGGER.info(f"[PLAYER:{guild_id}] Voice Info: None")

        # === QUEUE INFORMATION ===
        queue = getattr(player, "queue", [])
        queue_length = len(queue)
        LOGGER.info(f"[PLAYER:{guild_id}] Queue: {queue_length} tracks")

        if queue_length > 0:
            # Текущий трек (первый в очереди)
            current_track = queue[0]
            LOGGER.info(f"[PLAYER:{guild_id}] Current Track:")
            LOGGER.info(f"  - Title: {getattr(current_track, 'title', 'Unknown')}")
            LOGGER.info(f"  - Author: {getattr(current_track, 'author', 'Unknown')}")
            LOGGER.info(f"  - Length: {getattr(current_track, 'length', 0)}ms")
            LOGGER.info(f"  - URI: {getattr(current_track, 'uri', 'N/A')}")
            LOGGER.info(f"  - Requester: {getattr(current_track, 'requester', 'N/A')}")
            LOGGER.info(
                f"  - Encoded: {getattr(current_track, 'encoded', 'N/A')[:30]}..."
            )

            # Следующие треки
            if queue_length > 1:
                LOGGER.info(
                    f"[PLAYER:{guild_id}] Next {min(3, queue_length - 1)} tracks:"
                )
                for idx, track in enumerate(queue[1:4], 1):
                    title = getattr(track, "title", "Unknown")
                    author = getattr(track, "author", "Unknown")
                    LOGGER.info(f"  {idx}. {title} - {author}")

        # === PLAYBACK SETTINGS ===
        playback_settings = {
            "volume": getattr(player, "_volume", 100),
            "repeat": getattr(player, "_repeat", False),
            "queue_repeat": getattr(player, "_queue_repeat", False),
            "shuffle": getattr(player, "_shuffle", False),
        }

        LOGGER.info(f"[PLAYER:{guild_id}] Playback Settings:")
        for key, value in playback_settings.items():
            LOGGER.info(f"  - {key}: {value}")

        # === FILTERS ===
        filters = getattr(player, "_filters", None)
        if filters:
            filters_payload = getattr(filters, "_payload", {})
            if filters_payload:
                LOGGER.info(f"[PLAYER:{guild_id}] Active Filters:")
                for filter_key, filter_value in filters_payload.items():
                    LOGGER.info(f"  - {filter_key}: {filter_value}")
            else:
                LOGGER.info(f"[PLAYER:{guild_id}] Filters: No active filters")
        else:
            LOGGER.info(f"[PLAYER:{guild_id}] Filters: None")

        # === REST & NODE REFERENCE ===
        LOGGER.info(f"[PLAYER:{guild_id}] References:")
        LOGGER.info(f"  - Node: {getattr(player, 'node', 'N/A')}")
        LOGGER.info(f"  - REST: {getattr(player, 'rest', 'N/A')}")
        LOGGER.info(f"  - Loop: {getattr(player, 'loop', 'N/A')}")

    @node_health_monitor.before_loop
    async def before_health_monitor(self):
        """Wait for bot to be ready before starting monitor."""
        await self.bot.wait_until_ready()
        LOGGER.info("[HEALTH] Health monitor started")

    def _create_debug_handler(self, event_name: str):
        """Create a debug handler for specific event type."""

        async def debug_handler(event: Any) -> None:
            LOGGER.debug(f"[{event_name}] {event}")
            if hasattr(event, "__dict__"):
                LOGGER.debug(f"[{event_name}] Details: {event.__dict__}")

        return debug_handler

    async def handle_track_start(self, event: TrackStartEvent) -> None:
        """Handle track start event."""
        guild_id = event.guild_id
        self.sessions.setdefault(guild_id, MusicSession(guild_id=guild_id))
        self._track_start_times[guild_id] = utcnow()

    async def handle_track_end(self, event: TrackEndEvent) -> None:
        """Handle track end event."""
        guild_id = event.guild_id
        session = self.sessions.get(guild_id)
        if not session:
            return

        start_time = self._track_start_times.get(guild_id)
        if not start_time:
            return

        elapsed = (utcnow() - start_time).total_seconds()
        reason = event.reason

        skipped = False
        if reason == "finished":
            skipped = False
        elif reason in ("stopped", "replaced") and elapsed >= 20:
            skipped = True
        else:
            return

        track = cast(Track | list[Track], event.track)
        if track and isinstance(track, list):  # wtf why is there list in event.
            track = track[0]
        if isinstance(track, lavaplay.Track):
            session.tracks.append(
                TrackInfo(
                    title=track.title,
                    uri=track.uri,
                    skipped=skipped,
                    requester_id=track.requester,
                )
            )

    async def handle_websocket_closed(self, event: Any) -> None:
        """Handle WebSocket closed events from Discord voice gateway."""
        guild_id = getattr(event, "guild_id", None)
        code = getattr(event, "code", None)
        reason = getattr(event, "reason", "Unknown")
        by_remote = getattr(event, "by_remote", False)

        LOGGER.debug(
            "[WEBSOCKET CLOSED] Guild %s - Code: %s, Reason: %s, By Remote: %s",
            guild_id,
            code,
            reason,
            by_remote,
        )

        if code and 4000 <= code < 5000:
            LOGGER.error(
                "[WEBSOCKET CLOSED] Critical error code %s for guild %s - may need reconnect",
                code,
                guild_id,
            )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.node is not None:
            await self.node.close()
        for node in self.lavalink.nodes:
            await self.lavalink.destroy_node(node)

    async def _connect_node(self) -> None:
        """Connect to Lavalink node."""
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
            LOGGER.info("Node connected successfully")
        except Exception as e:
            LOGGER.error("Node connection failed: %s", e)
            raise NodeNotConnectedError(f"Failed to connect to Lavalink: {e}") from e

    async def _wait_for_connection(self) -> None:
        """Wait until node is fully connected."""
        while not self.node or not self.node.is_connect:
            await asyncio.sleep(0.1)

    async def check_connection(self) -> bool:
        """Check if node is connected and try to reconnect if not."""
        if not self.node or not self.node.is_connect:
            LOGGER.warning("Lavalink node not connected, attempting reconnect...")
            try:
                await self._connect_node()
                return True
            except Exception:
                return False
        return True

    async def _record_session_interaction(
        self, guild_id: int, text_channel_id: int | None, requester_id: int | None
    ) -> None:
        """Record interaction for session tracking."""
        if text_channel_id and requester_id:
            session = self.sessions.setdefault(
                guild_id, MusicSession(guild_id=guild_id)
            )
            session.record_interaction(text_channel_id, requester_id)

    async def get_player(self, guild_id: int) -> Player:
        """Get existing player or create new one."""
        await self.initialize()
        if self.node is None:
            raise NodeNotConnectedError("Node is None after initialization")

        player = self.node.get_player(guild_id)
        if not player:
            player = self.node.create_player(guild_id)
        return player

    async def get_volume(self, guild_id: int) -> int:
        """Get volume for specific guild from DB."""
        volume_data = get_json(config.MUSIC_VOLUME_FILE) or {}
        return volume_data.get(str(guild_id), config.MUSIC_DEFAULT_VOLUME)

    async def save_volume(self, guild_id: int, volume: int) -> None:
        """Save volume to DB."""
        volume_data = get_json(config.MUSIC_VOLUME_FILE) or {}
        volume_data[str(guild_id)] = volume
        save_json(config.MUSIC_VOLUME_FILE, volume_data)
        LOGGER.debug(f"Saved volume for guild {guild_id}: {volume}")

    async def apply_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        """Apply volume to live player if exists."""
        try:
            player = self.node.get_player(guild_id) if self.node else None
            if player:
                await player.volume(volume)
                LOGGER.debug(f"Applied volume to player for guild {guild_id}: {volume}")
            return MusicResult(
                MusicResultStatus.SUCCESS, "Громкость изменена", data=volume
            )
        except Exception as e:
            LOGGER.warning(f"Failed to apply volume to player: {e}")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}", data=volume)

    async def set_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        """Save volume to DB and update live player if exists."""
        await self.save_volume(guild_id, volume)
        return await self.apply_volume(guild_id, volume)

    async def search_tracks(
        self, query: str
    ) -> list[lavaplay.Track] | lavaplay.PlayList | None:
        """Search for tracks."""
        if self.node is None:
            self.node = self.lavalink.default_node

        return await self.node.auto_search_tracks(query)

    async def _ensure_player_volume(self, guild_id: int) -> None:
        """Ensure player has correct volume from DB."""
        try:
            player = self.node.get_player(guild_id) if self.node else None
            if not player:
                return

            saved_volume = await self.get_volume(guild_id)
            LOGGER.debug(f"Setting volume for guild {guild_id}: {saved_volume}")
            await player.volume(saved_volume)
        except Exception as e:
            LOGGER.warning(f"Failed to ensure player volume: {e}")

    async def join(
        self, guild: discord.Guild, channel: VocalGuildChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        voice_client = guild.voice_client

        if not voice_client:
            try:
                await channel.connect(cls=LavalinkVoiceClient)
                return VoiceCheckResult.SUCCESS, None
            except Exception as e:
                LOGGER.error(f"Failed to connect to voice: {e}")
                return VoiceCheckResult.CONNECTION_FAILED, None

        if voice_client.channel != channel:
            try:
                if not isinstance(voice_client, discord.VoiceClient):
                    LOGGER.error("Voice client is not a VoiceClient")
                    return VoiceCheckResult.CONNECTION_FAILED, None
                old_channel = voice_client.channel
                await voice_client.move_to(channel)
                return VoiceCheckResult.MOVED_CHANNELS, old_channel
            except Exception:
                LOGGER.exception("Failed to move voice channel")
                return VoiceCheckResult.CONNECTION_FAILED, None

        return VoiceCheckResult.ALREADY_CONNECTED, None

    async def end_session(self, guild_id: int) -> None:
        """End session and dispatch summary event."""
        session = self.sessions.pop(guild_id, None)
        LOGGER.debug(f"End session for guild {guild_id}: {session}")
        self._track_start_times.pop(guild_id, None)

        if not session or not session.tracks:
            return
        main_channel_id = None
        if session.channel_usage:
            main_channel_id = max(session.channel_usage, key=session.channel_usage.get)  # type: ignore

        if main_channel_id:
            self.bot.dispatch("music_session_end", guild_id, session, main_channel_id)

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave voice channel."""
        if not guild.voice_client:
            return MusicResult(MusicResultStatus.FAILURE, "Не подключен к войсу")

        try:
            await self.stop_player(guild.id)
            await self.end_session(guild.id)
            await guild.voice_client.disconnect(force=True)
            return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")
        except Exception as e:
            LOGGER.exception("Error leaving voice")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def play(
        self,
        guild: discord.Guild,
        voice_channel: VocalGuildChannel,
        query: str,
        requester_id: int,
        text_channel_id: int | None = None,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        """Play a track or playlist.

        Returns VoiceJoinResult only if failed to join
        """
        check_result, from_channel = await self.join(guild, voice_channel)
        if check_result.status is not MusicResultStatus.SUCCESS:
            return MusicResult(
                check_result.status,
                check_result.value,
                data=(check_result, from_channel),
            )

        try:
            player = await self.get_player(guild.id)
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Failed to get player: {e}")

        current_vol = await self.get_volume(guild.id)
        try:
            await player.volume(current_vol)
        except lavaplay.VolumeError:
            LOGGER.warning("Failed to set volume to %s", current_vol)

        try:
            tracks = await self.search_tracks(query)
            if not tracks:
                return MusicResult(MusicResultStatus.FAILURE, "Ничего не найдено")

            if isinstance(tracks, lavaplay.PlayList):
                for track in tracks.tracks:
                    await player.play(track, requester=requester_id)

                if text_channel_id:
                    session = self.sessions.setdefault(
                        guild.id, MusicSession(guild_id=guild.id)
                    )
                    session.record_interaction(text_channel_id, requester_id)

                return MusicResult(
                    MusicResultStatus.SUCCESS,
                    "Playlist added",
                    data={"type": "playlist", "playlist": tracks},
                )

            track = tracks[0]
            is_playing_before = player.is_playing
            await player.play(track, requester=requester_id)
            if text_channel_id:
                session = self.sessions.setdefault(
                    guild.id, MusicSession(guild_id=guild.id)
                )
                session.record_interaction(text_channel_id, requester_id)

            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Track processed",
                data={
                    "type": "track",
                    "track": track,
                    "playing": is_playing_before,
                },
            )

        except Exception as e:
            LOGGER.exception("Error in play")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def stop_player(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        """Stop player and clear queue."""
        try:
            player = await self.get_player(guild_id)
            await player.stop()

            await self._record_session_interaction(
                guild_id, text_channel_id, requester_id
            )

            return MusicResult(MusicResultStatus.SUCCESS, "Stopped and cleared queue")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def skip_track(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[SkipTrackData]:
        """Skip current track."""
        try:
            player = await self.get_player(guild_id)
            skipped_track = player.queue[0] if player.queue else None
            up_next = player.queue[1] if len(player.queue) > 1 else None
            await player.skip()

            await self._record_session_interaction(
                guild_id, text_channel_id, requester_id
            )

            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Skipped track",
                data={"before": skipped_track, "after": up_next},
            )
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def pause_player(self, guild_id: int) -> MusicResult[None]:
        try:
            player = await self.get_player(guild_id)
            await player.pause(True)
            return MusicResult(MusicResultStatus.SUCCESS, "Paused")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def resume_player(self, guild_id: int) -> MusicResult[None]:
        try:
            player = await self.get_player(guild_id)
            await player.pause(False)
            return MusicResult(MusicResultStatus.SUCCESS, "Resumed")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def shuffle_queue(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        try:
            player = await self.get_player(guild_id)
            player.shuffle()

            await self._record_session_interaction(
                guild_id, text_channel_id, requester_id
            )

            return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def set_repeat(
        self,
        guild_id: int,
        mode: RepeatMode | None,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RepeatModeData]:
        try:
            player = await self.get_player(guild_id)

            current_state = getattr(player, "_queue_repeat", False)
            current_mode = RepeatMode.QUEUE if current_state else RepeatMode.OFF

            if mode is None:
                mode = (
                    RepeatMode.OFF
                    if current_mode is RepeatMode.QUEUE
                    else RepeatMode.QUEUE
                )

            if mode is RepeatMode.QUEUE:
                player.queue_repeat(True)
            else:
                player.queue_repeat(False)

            await self._record_session_interaction(
                guild_id, text_channel_id, requester_id
            )

            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Repeat updated",
                data={"mode": mode, "previous": current_mode},
            )
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def get_queue(self, guild_id: int) -> MusicResult[Player]:
        """Get current queue data."""
        try:
            player = await self.get_player(guild_id)
            if not player.queue:
                return MusicResult(MusicResultStatus.FAILURE, "Очередь пуста")
            return MusicResult(
                MusicResultStatus.SUCCESS, "Queue retrieved", data=player
            )
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def rotate_current_track(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        """Move current track to the end of the queue and skip."""
        try:
            player = await self.get_player(guild_id)
            current = player.queue[0] if player.queue else None
            if not current:
                return MusicResult(MusicResultStatus.FAILURE, "Очередь пуста")
            await player.play(player.queue[0], requester=int(current.requester or "0"))
            await player.skip()

            await self._record_session_interaction(
                guild_id, text_channel_id, requester_id
            )

            new_current = player.queue[0] if player.queue else None
            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Rotated track",
                data={"skipped": current, "next": new_current},
            )
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def get_queue_duration(self, guild_id: int) -> int:
        """Get total duration of current track + queue in milliseconds."""
        try:
            player = await self.get_player(guild_id)
            queue_duration = sum(t.length for t in player.queue)
            LOGGER.debug(f"Queue duration for guild {guild_id}: {queue_duration} ms.")
            return queue_duration
        except Exception:
            return 0
