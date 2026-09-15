from dataclasses import dataclass

from discord.ext import commands

from api.music.healer import SessionHealer
from api.music.service.connection_manager import ConnectionManager
from api.music.service.core_service import CoreMusicService
from api.music.service.playback_events import PlaybackEventHandlers
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from api.music.service.voice_lifecycle import VoiceLifecycleHandlers
from cogs.music.views.controller import TrackControllerManager
from repositories.volume_repository import VolumeRepository


@dataclass(frozen=True, slots=True)
class MusicComponents:
    """Stateful objects owned by one music cog instance."""

    service: CoreMusicService
    connection: ConnectionManager
    state: StateManager
    volumes: VolumeRepository
    controllers: TrackControllerManager
    ui: UIOrchestrator
    healer: SessionHealer
    playback_events: PlaybackEventHandlers
    voice_lifecycle: VoiceLifecycleHandlers


def create_music_components(bot: commands.Bot) -> MusicComponents:
    """Build the explicit music object graph for one cog lifecycle."""
    connection = ConnectionManager(bot)
    state = StateManager()
    volumes = VolumeRepository()
    controllers = TrackControllerManager(bot, connection)
    ui = UIOrchestrator(bot, controllers, state)
    healer = SessionHealer(bot, connection, state, volumes, ui)
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
        volumes,
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
