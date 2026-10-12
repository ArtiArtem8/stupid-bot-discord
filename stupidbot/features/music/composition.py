from dataclasses import dataclass

from discord.ext import commands

from stupidbot.features.music.discord.controller_delivery import UIOrchestrator
from stupidbot.features.music.discord.views.controller import TrackControllerManager
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.runtime.connection import ConnectionManager
from stupidbot.features.music.runtime.playback_events import PlaybackEventHandlers
from stupidbot.features.music.runtime.recovery import SessionHealer
from stupidbot.features.music.runtime.state import StateManager
from stupidbot.features.music.runtime.voice_lifecycle import VoiceLifecycleHandlers
from stupidbot.features.music.service import CoreMusicService
from stupidbot.features.music.volume import VolumeSettings


@dataclass(frozen=True, slots=True)
class MusicComponents:
    """Stateful objects owned by one music cog instance."""

    service: CoreMusicService
    connection: ConnectionManager
    state: StateManager
    volumes: VolumeStore
    controllers: TrackControllerManager
    ui: UIOrchestrator
    healer: SessionHealer
    playback_events: PlaybackEventHandlers
    voice_lifecycle: VoiceLifecycleHandlers


def create_music_components(bot: commands.Bot, volumes: VolumeStore) -> MusicComponents:
    """Build the explicit music object graph for one cog lifecycle."""
    connection = ConnectionManager(bot)
    state = StateManager()
    controllers = TrackControllerManager(bot, connection)
    ui = UIOrchestrator(bot, controllers, state)
    volume_settings = VolumeSettings(volumes)
    healer = SessionHealer(bot, connection, state, volume_settings, ui)
    voice_lifecycle = VoiceLifecycleHandlers(bot, connection, state, ui, healer)
    playback_events = PlaybackEventHandlers(
        bot,
        connection,
        state,
        ui,
        voice_lifecycle.is_healing,
    )
    service = CoreMusicService(
        bot,
        connection,
        state,
        volume_settings,
        playback_events,
        voice_lifecycle,
        ui,
    )
    return MusicComponents(
        service=service,
        connection=connection,
        state=state,
        volumes=volumes,
        controllers=controllers,
        ui=ui,
        healer=healer,
        playback_events=playback_events,
        voice_lifecycle=voice_lifecycle,
    )
