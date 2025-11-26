"""Music API for Lavalink integration."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, TypedDict, cast, override

import discord
import lavaplay  # type: ignore
from discord.channel import VocalGuildChannel
from discord.ext import commands
from lavaplay.player import Player  # type: ignore

import config
from utils.json_utils import get_json, save_json

LOGGER = logging.getLogger(__name__)

type VoiceCheckData = (
    VocalGuildChannel | tuple[VocalGuildChannel, VocalGuildChannel] | None
)
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


@dataclass(frozen=True, slots=True)
class MusicResult[T]:
    """Result of a music operation."""

    status: MusicResultStatus
    message: str
    data: T | None = None

    @property
    def is_success(self) -> bool:
        return self.status == MusicResultStatus.SUCCESS


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

        await player.raw_voice_server_update(
            data.get("endpoint", "missing"), data.get("token", "missing")
        )

    @override
    async def on_voice_state_update(self, data: dict[str, Any]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        LOGGER.debug("[VOICE STATE UPDATE] Received data: %s", data)
        if self.lavalink is None:
            return

        player = self._get_player(self.lavalink)
        channel_id = cast(
            str | int | None,
            data.get("channel_id"),
        )

        if player is None:
            LOGGER.critical("Player is not initialized!")
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
        if self.channel and channel_id != self.channel.id:
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

    async def initialize(self) -> None:
        """Initialize Lavalink node."""
        if self.node and self.node.is_connect:
            return
        await self._connect_node()

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.node is not None:
            await self.node.close()
        for node in self.lavalink.nodes:
            self.lavalink.destroy_node(node)

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
    ) -> tuple[VoiceCheckResult, VoiceCheckData]:
        """Join a voice channel."""
        voice_client = guild.voice_client

        if not voice_client:
            try:
                await channel.connect(cls=LavalinkVoiceClient)
                return VoiceCheckResult.SUCCESS, channel
            except Exception as e:
                LOGGER.error(f"Failed to connect to voice: {e}")
                return VoiceCheckResult.CONNECTION_FAILED, channel

        if voice_client.channel != channel:
            try:
                old_channel = voice_client.channel
                if not isinstance(voice_client, discord.VoiceClient):
                    LOGGER.error("Voice client is not a VoiceClient")
                    return VoiceCheckResult.CONNECTION_FAILED, channel
                await voice_client.move_to(channel)
                return VoiceCheckResult.MOVED_CHANNELS, (
                    cast(VocalGuildChannel, old_channel),
                    channel,
                )
            except Exception:
                LOGGER.exception("Failed to move voice channel")
                return VoiceCheckResult.CONNECTION_FAILED, channel

        return VoiceCheckResult.ALREADY_CONNECTED, channel

    async def leave(self, guild: discord.Guild) -> MusicResult[None]:
        """Leave voice channel."""
        if not guild.voice_client:
            return MusicResult(MusicResultStatus.FAILURE, "Не подключен к войсу")

        try:
            await self.stop_player(guild.id)
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
    ) -> MusicResult[PlayResponseData]:
        """Play a track or playlist."""
        check_result, _ = await self.join(guild, voice_channel)
        if not check_result.is_success and check_result not in (
            VoiceCheckResult.SUCCESS,
            VoiceCheckResult.ALREADY_CONNECTED,
            VoiceCheckResult.MOVED_CHANNELS,
        ):
            return MusicResult(MusicResultStatus.FAILURE, check_result.msg)

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
                await player.play_playlist(tracks)

                return MusicResult(
                    MusicResultStatus.SUCCESS,
                    "Playlist added",
                    data={"type": "playlist", "playlist": tracks},
                )
            else:
                track = tracks[0]
                is_playing_before = player.is_playing

                await player.play(track, requester=requester_id)

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

    async def stop_player(self, guild_id: int) -> MusicResult[None]:
        """Stop player and clear queue."""
        try:
            player = await self.get_player(guild_id)
            await player.stop()
            return MusicResult(MusicResultStatus.SUCCESS, "Stopped and cleared queue")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def skip_track(self, guild_id: int) -> MusicResult[SkipTrackData]:
        """Skip current track."""
        try:
            player = await self.get_player(guild_id)
            skipped_track = player.queue[0] if player.queue else None
            up_next = player.queue[1] if len(player.queue) > 1 else None
            await player.skip()
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

    async def shuffle_queue(self, guild_id: int) -> MusicResult[None]:
        try:
            player = await self.get_player(guild_id)
            player.shuffle()
            return MusicResult(MusicResultStatus.SUCCESS, "Shuffled")
        except Exception as e:
            return MusicResult(MusicResultStatus.ERROR, f"Error: {e}")

    async def set_repeat(
        self, guild_id: int, mode: RepeatMode | None
    ) -> MusicResult[RepeatModeData]:
        try:
            player = await self.get_player(guild_id)

            current_state = getattr(player, "_queue_repeat", False)
            current_mode = RepeatMode.QUEUE if current_state else RepeatMode.OFF

            if mode is None:
                mode = (
                    RepeatMode.OFF
                    if current_mode == RepeatMode.QUEUE
                    else RepeatMode.QUEUE
                )

            if mode == RepeatMode.QUEUE:
                player.queue_repeat(True)
            else:
                player.queue_repeat(False)

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

    async def rotate_current_track(self, guild_id: int) -> MusicResult[RotateTrackData]:
        """Move current track to the end of the queue and skip."""
        try:
            player = await self.get_player(guild_id)
            current = player.queue[0] if player.queue else None
            if not current:
                return MusicResult(MusicResultStatus.FAILURE, "Очередь пуста")
            await player.play(player.queue[0], requester=int(current.requester or "0"))
            await player.skip()
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
            return queue_duration
        except Exception:
            return 0
