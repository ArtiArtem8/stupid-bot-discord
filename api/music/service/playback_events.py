from __future__ import annotations

import copy
import logging
from collections.abc import Callable

import mafic
from discord.ext import commands
from mafic.typings import LavalinkException

from api.music.errors import EXPECTED_LAVALINK_IO_ERRORS, compact_external_log_text
from api.music.models import (
    PLAYBACK_USER_DATA_KEY,
    ControllerDestroyReason,
    PlaybackAttempt,
    TrackExceptionPayload,
)
from api.music.player import MusicPlayer
from api.music.service.connection_manager import ConnectionManager
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator

logger = logging.getLogger(__name__)

TRACK_TITLE_TEXT_LIMIT = 160


class PlaybackEventHandlers:
    """Correlate Mafic playback events before applying application side effects."""

    def __init__(
        self,
        bot: commands.Bot,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
        ui_orchestrator: UIOrchestrator,
        is_healing: Callable[[int], bool],
    ) -> None:
        self.bot = bot
        self.connection = connection_manager
        self.state = state_manager
        self.ui = ui_orchestrator
        self._is_healing = is_healing
        self._load_failures: dict[int, set[int]] = {}
        self._setup_done = False

    def setup(self) -> None:
        """Register Mafic playback listeners once."""
        if self._setup_done:
            logger.warning("PlaybackEventHandlers setup called multiple times.")
            return

        self.bot.add_listener(self._on_track_start, "on_track_start")
        self.bot.add_listener(self._on_track_end, "on_track_end")
        self.bot.add_listener(self._on_track_exception, "on_track_exception")
        self.bot.add_listener(self._on_track_stuck, "on_track_stuck")
        self._setup_done = True

    def cleanup(self) -> None:
        """Remove playback listeners and transient correlation state."""
        if not self._setup_done:
            return

        self.bot.remove_listener(self._on_track_start, "on_track_start")
        self.bot.remove_listener(self._on_track_end, "on_track_end")
        self.bot.remove_listener(self._on_track_exception, "on_track_exception")
        self.bot.remove_listener(self._on_track_stuck, "on_track_stuck")
        self._load_failures.clear()
        self._setup_done = False
        logger.info("PlaybackEventHandlers listeners removed.")

    def _event_token(self, track: mafic.Track, event_name: str) -> str | None:
        token = track.user_data.get(PLAYBACK_USER_DATA_KEY)
        if isinstance(token, str) and token:
            return token
        logger.warning(
            "Ignoring untagged %s guild track_source=%s track_id=%s",
            event_name,
            track.source,
            track.identifier,
        )
        return None

    def _should_handle_player_event(
        self,
        player: MusicPlayer,
        event_name: str,
    ) -> bool:
        guild_id = player.guild.id
        if self._is_healing(guild_id) or not self.connection.is_current_player(player):
            logger.debug(
                "Ignoring %s from non-current or healing player for guild %s",
                event_name,
                guild_id,
            )
            return False
        return True

    async def _on_track_start(self, event: mafic.TrackStartEvent[MusicPlayer]) -> None:
        if not self._should_handle_player_event(event.player, "track_start"):
            return
        token = self._event_token(event.track, "TrackStartEvent")
        if token is None:
            return

        player = event.player
        attempt = await player.resolve_track_start(token)
        if attempt is None:
            return

        self.state.record_track_start(player.guild.id, attempt)
        logger.debug(
            "Track started in guild %d: attempt=%s",
            player.guild.id,
            attempt.attempt_id,
        )
        await self.ui.spawn_controller(player, attempt)

    async def _on_track_exception(
        self, event: mafic.TrackExceptionEvent[MusicPlayer]
    ) -> None:
        if not self._should_handle_player_event(event.player, "track_exception"):
            return
        token = self._event_token(event.track, "TrackExceptionEvent")
        if token is None:
            return

        player = event.player
        attempt = await player.claim_track_exception(token)
        if attempt is None:
            logger.debug(
                "Ignoring duplicate or stale TrackExceptionEvent guild=%s token=%s",
                player.guild.id,
                token,
            )
            return

        reason, severity = self._extract_exception_details(event.exception)
        message = compact_external_log_text(event.exception.get("message"))
        cause = compact_external_log_text(event.exception.get("cause"))
        title = compact_external_log_text(
            attempt.entry.track.title,
            limit=TRACK_TITLE_TEXT_LIMIT,
        )
        position = player.position if player.current_attempt is attempt else None
        logger.warning(
            "Track playback/source failure: %s",
            {
                "guild": player.guild.id,
                "attempt": attempt.attempt_id,
                "source": attempt.entry.track.source,
                "id": attempt.entry.track.identifier,
                "title": title,
                "position_ms": position,
                "length_ms": attempt.entry.track.length,
                "severity": severity,
                "message": message,
                "cause": cause,
            },
        )

        self._load_failures.setdefault(player.guild.id, set()).add(attempt.attempt_id)
        self._dispatch_track_exception(player, attempt, reason, severity)
        await self.ui.controller.destroy_for_guild(
            player.guild.id,
            ControllerDestroyReason.TRACK_EXCEPTION,
            expected_attempt_id=attempt.attempt_id,
        )

    async def _on_track_stuck(self, event: mafic.TrackStuckEvent[MusicPlayer]) -> None:
        if not self._should_handle_player_event(event.player, "track_stuck"):
            return
        token = self._event_token(event.track, "TrackStuckEvent")
        if token is None:
            return

        player = event.player
        attempt = await player.resolve_exception_attempt(token)
        if attempt is None:
            return
        title = compact_external_log_text(
            attempt.entry.track.title,
            limit=TRACK_TITLE_TEXT_LIMIT,
        )
        position = player.position if player.current_attempt is attempt else None
        logger.warning(
            "Track stuck: %s",
            {
                "guild": player.guild.id,
                "attempt": attempt.attempt_id,
                "source": attempt.entry.track.source,
                "id": attempt.entry.track.identifier,
                "title": title,
                "position_ms": position,
                "threshold_ms": event.threshold_ms,
            },
        )
        await self.ui.controller.destroy_for_guild(
            player.guild.id,
            ControllerDestroyReason.TRACK_STUCK,
            expected_attempt_id=attempt.attempt_id,
        )

    async def _on_track_end(self, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
        if not self._should_handle_player_event(event.player, "track_end"):
            return
        token = self._event_token(event.track, "TrackEndEvent")
        if token is None:
            return

        player = event.player
        reason = event.reason
        try:
            outcome = await player.handle_track_end(token, reason)
        except EXPECTED_LAVALINK_IO_ERRORS as exc:
            await self.connection.invalidate_player(
                player,
                context=f"track_end_transition_{reason.value}",
                error=type(exc).__name__,
            )
            return

        if outcome.is_stale or outcome.ended_attempt is None:
            return

        ended = outcome.ended_attempt
        logger.debug("Track ended: attempt=%s reason=%s", ended.attempt_id, reason)
        self.state.record_history(player.guild.id, ended, reason)

        failures = self._load_failures.setdefault(player.guild.id, set())
        if reason is mafic.EndReason.LOAD_FAILED and ended.attempt_id not in failures:
            track = ended.entry.track
            title = compact_external_log_text(track.title, limit=TRACK_TITLE_TEXT_LIMIT)
            logger.warning(
                "Track playback/source failure: %s",
                {
                    "guild": player.guild.id,
                    "attempt": ended.attempt_id,
                    "source": track.source,
                    "id": track.identifier,
                    "title": title,
                    "reason": "load_failed",
                },
            )
            self._dispatch_track_exception(
                player,
                ended,
                reason="Lavalink: загрузка не удалась",
                severity=None,
            )
        failures.discard(ended.attempt_id)
        if not failures:
            self._load_failures.pop(player.guild.id, None)

        await self.ui.controller.destroy_for_guild(
            player.guild.id,
            ControllerDestroyReason.TRACK_END,
            expected_attempt_id=ended.attempt_id,
        )

    def _extract_exception_details(
        self, exception: LavalinkException
    ) -> tuple[str, str | None]:
        message = exception.get("message") or exception.get("cause")
        reason = str(message) if message else "Неизвестная ошибка"
        severity = exception.get("severity")
        return reason, severity

    def _dispatch_track_exception(
        self,
        player: MusicPlayer,
        attempt: PlaybackAttempt,
        reason: str,
        severity: str | None,
    ) -> None:
        track = attempt.entry.track
        requester = attempt.entry.requester
        payload = TrackExceptionPayload(
            guild_id=player.guild.id,
            track=copy.copy(track),
            reason=reason,
            severity=severity,
            requester_id=requester.user_id if requester else None,
            channel_id=requester.channel_id if requester else None,
        )
        self.bot.dispatch("music_track_exception", payload)
