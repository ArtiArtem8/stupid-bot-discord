"""Music API for Mafic (Lavalink) integration."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import discord
import mafic
from discord.channel import VocalGuildChannel
from discord.ext import commands
from discord.utils import utcnow

import config
from utils.json_utils import get_json, save_json

if TYPE_CHECKING:
    from discord.abc import Connectable

LOGGER = logging.getLogger(__name__)


type Track = mafic.Track
type Playlist = mafic.Playlist
type SearchResult = list[Track] | Playlist | None


class MusicError(Exception):
    """Base exception for Music API errors."""


class NodeNotConnectedError(MusicError):
    """Raised when Lavalink node is not connected."""


class PlaylistResponseData(TypedDict):
    type: Literal["playlist"]
    playlist: Playlist


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
    TRACK = "track"
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
            case (
                VoiceCheckResult.ALREADY_CONNECTED
                | VoiceCheckResult.MOVED_CHANNELS
                | VoiceCheckResult.SUCCESS
            ):
                return MusicResultStatus.SUCCESS
            case VoiceCheckResult.CONNECTION_FAILED:
                return MusicResultStatus.ERROR
            case _:
                return MusicResultStatus.FAILURE


type VoiceJoinResult = tuple[VoiceCheckResult, VocalGuildChannel | None]


@dataclass(slots=True)
class TrackInfo:
    title: str
    uri: str
    skipped: bool = False
    requester_id: int | None = None


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


class QueueManager:
    """Manages the queue of tracks."""

    def __init__(self):
        self._queue: deque[Track] = deque()

    def __len__(self) -> int:
        """Return the number of tracks in the queue.
        equivalent to len(self.tracks).
        """
        return len(self._queue)

    @property
    def tracks(self) -> deque[Track]:
        return self._queue

    @property
    def next(self) -> Track | None:
        if not self._queue:
            return None
        return self._queue[0]

    def add(self, tracks: list[Track] | Track, at_front: bool = False) -> None:
        """Add track(s) to the queue."""
        if isinstance(tracks, list):
            if at_front:
                self._queue.extendleft(reversed(tracks))
            else:
                self._queue.extend(tracks)
        else:
            if at_front:
                self._queue.appendleft(tracks)
            else:
                self._queue.append(tracks)

    def pop_next(self) -> Track | None:
        """Pop the next track from the queue."""
        if not self._queue:
            return None
        return self._queue.popleft()

    def shuffle(self) -> None:
        """Shuffle the queue."""
        if len(self._queue) < 2:
            return
        temp = list(self._queue)
        random.shuffle(temp)
        self._queue = deque(temp)

    def clear(self) -> None:
        """Clear the queue."""
        self._queue.clear()

    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0

    @property
    def duration_ms(self) -> int:
        """Total duration of tracks in queue."""
        return sum(t.length for t in self._queue)


class RepeatManager:
    """Manages repeat mode."""

    def __init__(self):
        self._mode: RepeatMode = RepeatMode.OFF

    @property
    def mode(self) -> RepeatMode:
        return self._mode

    @mode.setter
    def mode(self, value: RepeatMode) -> None:
        self._mode = value

    def toggle(self) -> RepeatMode:
        """Toggle between OFF and QUEUE."""
        self._mode = (
            RepeatMode.QUEUE if self._mode is RepeatMode.OFF else RepeatMode.OFF
        )
        return self._mode


class MusicPlayer(mafic.Player[discord.Client]):
    """Custom Mafic Player with Queue, Repeat, and Requester tracking.

    Mafic's Player is a voice protocol that handles the Lavalink<->Discord
    voice handshake automatically. We extend it to add queue management.
    """

    def __init__(self, client: discord.Client, channel: Connectable) -> None:
        super().__init__(client, channel)

        self.queue = QueueManager()
        self.repeat = RepeatManager()

        # self.text_channel_id: int | None = None
        self._requester_map: dict[str, int] = {}

    async def move_to(
        self, channel: discord.abc.Snowflake | None, *, timeout: float = 30.0
    ) -> None:
        """Move to a different voice channel."""
        if channel is None:
            await self.disconnect()
            return

        if not isinstance(self.channel, (discord.VoiceChannel, discord.StageChannel)):
            msg = "Voice channel must be a VoiceChannel or StageChannel."
            raise TypeError(msg)

        if self.channel and channel.id == self.channel.id:
            return

        self._voice_state_update_event.clear()
        self._voice_server_update_event.clear()

        await self.guild.change_voice_state(channel=channel)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._voice_state_update_event.wait(),
                    self._voice_server_update_event.wait(),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Timed out moving to channel %s in guild %s",
                channel.id,
                self.guild.id,
            )
            raise

    def set_requester(self, track: Track, requester_id: int) -> None:
        """Associate a requester with a track."""
        self._requester_map[track.id] = requester_id

    def get_requester(self, track: Track) -> int | None:
        """Get the requester ID for a track."""
        return self._requester_map.get(track.id)

    def clear_queue(self) -> None:
        """Clear the queue and requester map."""
        self.queue.clear()
        self._requester_map.clear()

    async def advance(self, *, force_skip: bool = False) -> Track | None:
        """Advance to the next state (next track or repeat).

        :param force_skip: If True, ignores RepeatMode.TRACK and moves to next song.
        """
        current = self.current

        LOGGER.debug("Advancing in guild %s, current track: %s", self.guild.id, current)

        if not force_skip and self.repeat.mode is RepeatMode.TRACK and current:
            LOGGER.debug("Force skipping track %s", current)
            await self.play(current)
            return current

        if self.repeat.mode is RepeatMode.QUEUE and current:
            LOGGER.debug("Adding track %s to queue", current)
            self.queue.add(current)

        next_track = self.queue.pop_next()
        if not next_track:
            LOGGER.debug("No next track in queue, stopping")
            return None

        LOGGER.debug("Playing next track: %s", next_track)
        await self.play(next_track)
        return next_track

    async def skip(self) -> Track | None:
        """Skip the current track.
        This forces the player to advance to the next track, ignoring RepeatMode.TRACK.
        """
        skipped_track = self.current

        next_track = await self.advance(force_skip=True)

        if not next_track:
            await self.stop()

        return skipped_track

    def clear_state(self) -> None:
        """Clear queue and state."""
        self.queue.clear()
        self._requester_map.clear()


def music_player_factory(client: discord.Client, connectable: Connectable):
    """Create a custom Mafic Player."""
    return MusicPlayer(client, connectable)


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    current: Track | None
    queue: tuple[Track, ...]
    repeat_mode: RepeatMode


class MusicAPI:
    """API for managing music playback via Mafic (Lavalink)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pool = mafic.NodePool(bot)
        self.sessions: dict[int, MusicSession] = {}
        self._track_start_times: dict[int, datetime] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize Lavalink node connection."""
        if self._initialized:
            return

        try:
            await self.pool.create_node(
                host=config.LAVALINK_HOST,
                port=config.LAVALINK_PORT,
                password=config.LAVALINK_PASSWORD,
                label="MAIN",
                secure=getattr(config, "LAVALINK_SECURE", False),
            )
            self._initialized = True
            self._setup_event_listeners()

            LOGGER.info("Mafic node pool initialized successfully")

        except Exception as e:
            LOGGER.exception("Failed to initialize Mafic node")
            raise NodeNotConnectedError(f"Failed to connect: {e}") from e

    def _setup_event_listeners(self) -> None:
        """Register event listeners with the bot."""
        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_node_ready, "on_node_ready")

    async def _on_node_ready(self, node: mafic.Node[commands.Bot]) -> None:
        """Handle node ready event."""
        LOGGER.info("Lavalink node '%s' is ready", node.label)

    async def _on_track_start(self, event: mafic.TrackStartEvent[MusicPlayer]) -> None:
        """Handle track start - record timing for skip detection."""
        guild_id = event.player.guild.id
        self.sessions.setdefault(guild_id, MusicSession(guild_id=guild_id))
        self._track_start_times[guild_id] = utcnow()
        LOGGER.debug("Track started in guild %d: %s", guild_id, event.track.title)

    async def _on_track_end(self, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
        """Handle track end - record history and auto-advance queue."""
        player = event.player
        guild_id = player.guild.id
        track = event.track
        reason = event.reason

        LOGGER.debug(
            "Track ended in guild %d: %s (reason: %s)", guild_id, track.title, reason
        )

        session = self.sessions.get(guild_id)
        start_time = self._track_start_times.pop(guild_id, None)

        if session and start_time:
            elapsed = (utcnow() - start_time).total_seconds()

            LOGGER.debug(
                "Track %s in guild %d played for %f seconds",
                track.title,
                guild_id,
                elapsed,
            )

            skipped = False
            if reason in (mafic.EndReason.STOPPED, mafic.EndReason.REPLACED):
                skipped = elapsed < 20

            session.tracks.append(
                TrackInfo(
                    title=track.title,
                    uri=track.uri or "",
                    skipped=skipped,
                    requester_id=player.get_requester(track),
                )
            )

        if reason in (mafic.EndReason.FINISHED, mafic.EndReason.LOAD_FAILED):
            LOGGER.debug("Auto-advancing to next track in guild %d", guild_id)
            await player.advance()

    def get_player(self, guild_id: int) -> MusicPlayer | None:
        """Get the MusicPlayer for a guild, if connected."""
        guild = self.bot.get_guild(guild_id)
        if guild and isinstance(guild.voice_client, MusicPlayer):
            return guild.voice_client
        return None

    async def get_volume(self, guild_id: int) -> int:
        """Get saved volume for guild."""
        data = get_json(config.MUSIC_VOLUME_FILE) or {}
        return data.get(str(guild_id), config.MUSIC_DEFAULT_VOLUME)

    async def save_volume(self, guild_id: int, volume: int) -> None:
        """Persist volume setting."""
        data = get_json(config.MUSIC_VOLUME_FILE) or {}
        data[str(guild_id)] = volume
        save_json(config.MUSIC_VOLUME_FILE, data)

    async def set_volume(self, guild_id: int, volume: int) -> MusicResult[int]:
        """Set and apply volume."""
        await self.save_volume(guild_id, volume)
        player = self.get_player(guild_id)
        if player:
            try:
                await player.set_volume(volume)
            except Exception as e:
                LOGGER.warning("Failed to apply volume: %s", e)
        return MusicResult(MusicResultStatus.SUCCESS, "Volume set", data=volume)

    # --- Connection Management ---

    async def join(
        self, guild: discord.Guild, channel: VocalGuildChannel
    ) -> VoiceJoinResult:
        """Join a voice channel."""
        LOGGER.debug("Joining channel: %s, %s", channel, type(channel))
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return VoiceCheckResult.INVALID_CHANNEL_TYPE, None

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
                old_channel = cast(VocalGuildChannel, voice_client.channel)
                await voice_client.move_to(channel)
                return VoiceCheckResult.MOVED_CHANNELS, old_channel

            await channel.connect(cls=music_player_factory)

            player = self.get_player(guild.id)
            if player:
                vol = await self.get_volume(guild.id)
                await player.set_volume(vol)

            return VoiceCheckResult.SUCCESS, None

        except asyncio.TimeoutError:
            LOGGER.warning("Voice connection timed out for guild %s", guild.id)
            return VoiceCheckResult.CONNECTION_FAILED, None
        except Exception:
            LOGGER.exception("Failed to join voice channel")
            return VoiceCheckResult.CONNECTION_FAILED, None

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave voice channel and cleanup."""
        player = self.get_player(guild.id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "Not connected")

        try:
            await self.end_session(guild.id)
            player.clear_queue()
            await player.disconnect()
            return MusicResult(MusicResultStatus.SUCCESS, "Disconnected")
        except Exception as e:
            LOGGER.exception("Error leaving voice")
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def end_session(self, guild_id: int) -> None:
        """End session and dispatch summary event."""
        session = self.sessions.pop(guild_id, None)
        self._track_start_times.pop(guild_id, None)

        if session and session.tracks:
            main_channel_id = (
                max(session.channel_usage, key=lambda k: session.channel_usage[k])
                if session.channel_usage
                else None
            )
            if main_channel_id:
                self.bot.dispatch(
                    "music_session_end", guild_id, session, main_channel_id
                )

    async def search_tracks(self, query: str, player: MusicPlayer) -> SearchResult:
        """Search for tracks using the node pool."""
        if not self.pool.nodes:
            await self.initialize()
        return await player.fetch_tracks(query)

    async def play(
        self,
        guild: discord.Guild,
        voice_channel: VocalGuildChannel,
        query: str,
        requester_id: int,
        text_channel_id: int | None = None,
    ) -> MusicResult[PlayResponseData | VoiceJoinResult]:
        """Play a track or playlist."""
        check_result, old_channel = await self.join(guild, voice_channel)
        if check_result.status is MusicResultStatus.ERROR:
            return MusicResult(
                check_result.status,
                "Connection failed",
                data=(check_result, old_channel),
            )

        player = self.get_player(guild.id)
        if not player:
            return MusicResult(MusicResultStatus.ERROR, "Player not available")

        # player.text_channel_id = text_channel_id
        await self._record_interaction(guild.id, text_channel_id, requester_id)

        try:
            result = await self.search_tracks(query, player)

            if not result:
                return MusicResult(MusicResultStatus.FAILURE, "Nothing found")

            if isinstance(result, mafic.Playlist):
                for track in result.tracks:
                    player.set_requester(track, requester_id)

                player.queue.add(result.tracks)
                if not player.current:
                    await player.advance()

                return MusicResult(
                    MusicResultStatus.SUCCESS,
                    "Playlist added",
                    data={"type": "playlist", "playlist": result},
                )

            track = result[0]
            player.set_requester(track, requester_id)

            is_playing = player.current is not None
            if is_playing:
                player.queue.add(track)
            else:
                await player.play(track)

            return MusicResult(
                MusicResultStatus.SUCCESS,
                "Track processed",
                data={"type": "track", "track": track, "playing": is_playing},
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
        """Stop playback and clear queue."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        player.clear_queue()
        await player.stop()
        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(MusicResultStatus.SUCCESS, "Stopped")

    async def skip_track(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[SkipTrackData]:
        """Skip the current track."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        current = player.current
        up_next = player.queue.next

        await player.skip()
        await self._record_interaction(guild_id, text_channel_id, requester_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Skipped",
            data={"before": current, "after": up_next},
        )

    async def pause_player(self, guild_id: int) -> MusicResult[None]:
        """Pause playback."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        await player.pause()
        return MusicResult(MusicResultStatus.SUCCESS, "Paused")

    async def resume_player(self, guild_id: int) -> MusicResult[None]:
        """Resume playback."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        await player.resume()
        return MusicResult(MusicResultStatus.SUCCESS, "Resumed")

    async def shuffle_queue(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[None]:
        """Shuffle the queue."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        player.queue.shuffle()
        await self._record_interaction(guild_id, text_channel_id, requester_id)
        return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")

    async def set_repeat(
        self,
        guild_id: int,
        mode: RepeatMode | None = None,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RepeatModeData]:
        """Set repeat mode."""
        player = self.get_player(guild_id)
        if not player:
            return MusicResult(MusicResultStatus.FAILURE, "No player")

        previous = player.repeat.mode

        if mode is None:
            player.repeat.toggle()
        else:
            player.repeat.mode = mode
        await self._record_interaction(guild_id, text_channel_id, requester_id)

        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Repeat updated",
            data={"mode": player.repeat.mode, "previous": previous},
        )

    async def get_queue(self, guild_id: int) -> MusicResult[QueueSnapshot]:
        player = self.get_player(guild_id)

        if not player or (not player.queue and not player.current):
            return MusicResult(MusicResultStatus.FAILURE, "Queue empty")

        snapshot = QueueSnapshot(
            current=player.current,
            queue=tuple(player.queue.tracks),
            repeat_mode=player.repeat.mode,
        )
        return MusicResult(MusicResultStatus.SUCCESS, "Retrieved", data=snapshot)

    async def rotate_current_track(
        self,
        guild_id: int,
        requester_id: int | None = None,
        text_channel_id: int | None = None,
    ) -> MusicResult[RotateTrackData]:
        """Move current track to end of queue and skip."""
        player = self.get_player(guild_id)
        if not player or not player.current:
            return MusicResult(MusicResultStatus.FAILURE, "Nothing playing")

        current = player.current
        player.queue.add(current)
        await player.skip()

        await self._record_interaction(guild_id, text_channel_id, requester_id)

        next_track = player.queue.next
        return MusicResult(
            MusicResultStatus.SUCCESS,
            "Rotated",
            data={"skipped": current, "next": next_track},
        )

    async def get_queue_duration(self, guild_id: int) -> int:
        """Get total queue duration in milliseconds."""
        player = self.get_player(guild_id)
        if not player:
            return 0
        total = player.queue.duration_ms
        if player.current:
            position = player.position or 0
            total += max(0, player.current.length - position)
        return total

    async def _record_interaction(
        self, guild_id: int, text_channel_id: int | None, requester_id: int | None
    ) -> None:
        """Record interaction for session analytics."""
        if text_channel_id and requester_id:
            session = self.sessions.setdefault(
                guild_id, MusicSession(guild_id=guild_id)
            )
            session.record_interaction(text_channel_id, requester_id)

    async def cleanup(self) -> None:
        """Cleanup on shutdown."""
        for guild in self.bot.guilds:
            if guild.voice_client:
                await guild.voice_client.disconnect(force=True)
