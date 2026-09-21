"""Discord observations and lifecycle only; all analytics live in api.voice.

The raw gateway listener preserves IDs/session fields that discord.py discards
when it cannot resolve a member or channel. Only voice and guild-create fields
are normalized; no gateway payload, token or member profile is logged or stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import override

import discord
from discord.ext import commands, tasks
from discord.utils import utcnow

import config
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceFact,
    VoiceJournalRecord,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from repositories.voice_journal import Submission, VoiceJournal
from utils.json_types import JsonObject, JsonValue, is_json_object

logger = logging.getLogger(__name__)


class VoiceCollectorCog(commands.Cog):
    """Own collection lifecycle and one journal; never replay or aggregate facts."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        journal: VoiceJournal | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.bot = bot
        self.journal = (
            journal
            if journal is not None
            else VoiceJournal(
                config.VOICE_PROBE_DIR,
                queue_size=config.VOICE_PROBE_EVENT_QUEUE_MAX,
                batch_size=config.VOICE_PROBE_WRITER_BATCH_MAX,
            )
        )
        self._monotonic = monotonic
        self._now = now
        self._boot_id = uuid.uuid4().hex
        self._sequence = 0
        self._online = False
        self._running = False
        self._last_tick: tuple[datetime, float] | None = None
        self._last_snapshot = 0.0

    @override
    async def cog_load(self) -> None:
        if not config.VOICE_PROBE_ENABLED:
            return
        self.journal.start()
        self._running = True
        self._record(VoiceLifecycle())
        self._heartbeat.start()
        self._maintenance.start()
        if self.bot.is_ready():
            self._resume()

    @override
    async def cog_unload(self) -> None:
        if not self._running:
            return
        self._online = False
        pending: list[asyncio.Task[None]] = []
        for loop in (self._heartbeat, self._maintenance):
            task = loop.get_task()
            if task is not None:
                loop.cancel()
                pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._running:
            self._record(VoiceLifecycle(stopped=True))
            self._running = False

        await self.journal.close()

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, message: str) -> None:
        if not self._running:
            return
        try:
            # Validate the library/JSON boundary before accessing fields.
            value: object = json.loads(message)  # pyright: ignore[reportAny]
            if not is_json_object(value):
                return
            self._gateway_fact(value)
        except (ValueError, TypeError):
            self._record(ObservationGap(self._now(), None, GapReason.UNKNOWN))
            logger.warning("Voice collector rejected an invalid gateway observation")
            logger.debug("Voice normalization traceback", exc_info=True)

    def _gateway_fact(self, envelope: JsonObject) -> None:
        event = envelope.get("t")
        if event not in ("VOICE_STATE_UPDATE", "GUILD_CREATE"):
            return
        payload = envelope.get("d")
        if not is_json_object(payload):
            raise ValueError("Missing gateway data")
        if event == "VOICE_STATE_UPDATE":
            guild_id = _id(payload.get("guild_id"))
            if guild_id is not None:
                state = self._raw_state(payload, guild_id)
                self._record(VoiceObservation(state), guild_id)
        else:
            self._raw_guild_snapshot(payload)

    def _raw_guild_snapshot(self, payload: JsonObject) -> None:
        guild_id = _id(payload.get("id"))
        states = payload.get("voice_states")
        if guild_id is None or not isinstance(states, list):
            return
        normalized: list[VoiceStateSnapshot] = []
        for state in states:
            if not is_json_object(state):
                raise ValueError("Invalid guild voice state")
            normalized.append(self._raw_state(state, guild_id, guild_data=payload))
        self._record(VoiceSnapshot(tuple(normalized)), guild_id)

    def _raw_state(
        self,
        payload: JsonObject,
        guild_id: int,
        *,
        guild_data: JsonObject | None = None,
    ) -> VoiceStateSnapshot:
        user_id = _id(payload.get("user_id"))
        if user_id is None:
            raise ValueError("Missing voice user ID")
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        user = member or self.bot.get_user(user_id)
        afk_channel = guild.afk_channel if guild is not None else None
        afk_channel_id = afk_channel.id if afk_channel is not None else None
        if guild_data is not None and "afk_channel_id" in guild_data:
            afk_channel_id = _id(guild_data["afk_channel_id"])
        return normalize_voice_state(
            payload,
            is_bot=user.bot if user is not None else None,
            afk_channel_id=afk_channel_id,
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._running:
            self._resume()

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        if self._running:
            self._resume()

    def _resume(self) -> None:
        self._online = True
        self._record(VoiceCheckpoint())
        self._snapshot_guilds()
        self._last_tick = (self._now(), self._monotonic())

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        if self._running and self._online:
            self._online = False
            self._record(
                ObservationGap(self._now(), None, GapReason.GATEWAY_DISCONNECT)
            )

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        if self._running and self._online:
            self._snapshot_guild(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self._running and self._online:
            self._snapshot_guild(guild)

    @commands.Cog.listener()
    async def on_guild_unavailable(self, guild: discord.Guild) -> None:
        if self._running:
            self._record(
                ObservationGap(
                    self._now(), None, GapReason.GATEWAY_DISCONNECT, guild.id
                ),
                guild.id,
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self._running:
            self._record(VoiceLifecycle(stopped=True), guild.id)

    @tasks.loop(seconds=config.VOICE_PROBE_HEARTBEAT_SECONDS)
    async def _heartbeat(self) -> None:
        self._heartbeat_once()

    def _heartbeat_once(self) -> None:
        moment, monotonic = self._now(), self._monotonic()
        if self._last_tick is not None:
            previous_at, previous_monotonic = self._last_tick
            elapsed = monotonic - previous_monotonic
            wall = (moment - previous_at).total_seconds()
            if (
                elapsed
                > max(
                    config.VOICE_PROBE_MONOTONIC_JUMP_SECONDS,
                    config.VOICE_PROBE_HEARTBEAT_SECONDS * 2,
                )
                or elapsed < 0
                or abs(wall - elapsed) > 5
            ):
                self._record(
                    ObservationGap(
                        min(previous_at, moment),
                        None,
                        GapReason.CLOCK_DISCONTINUITY,
                        known_bounds=False,
                    )
                )
                self._last_snapshot = monotonic - config.VOICE_PROBE_FULL_ANCHOR_SECONDS
        self._last_tick = moment, monotonic
        if not self._online:
            return
        self._record(VoiceCheckpoint())
        if monotonic - self._last_snapshot >= config.VOICE_PROBE_FULL_ANCHOR_SECONDS:
            self._snapshot_guilds()

    def _snapshot_guilds(self) -> None:
        for guild in self.bot.guilds:
            if not guild.unavailable:
                self._snapshot_guild(guild)
        self._last_snapshot = self._monotonic()

    def _snapshot_guild(self, guild: discord.Guild) -> None:
        # Public channel.voice_states omits unresolved channels. The guild cache
        # retains those user IDs; mark the result local/incomplete in that case.
        cached = guild._voice_states  # pyright: ignore[reportPrivateUsage]
        states = tuple(
            _cached_state(guild, user_id, state) for user_id, state in cached.items()
        )
        self._record(
            VoiceSnapshot(states, authoritative=all(s.channel_known for s in states)),
            guild.id,
        )

    @tasks.loop(hours=24)
    async def _maintenance(self) -> None:
        today = self._now().date()
        try:
            await self.journal.compact(
                before_day=today - timedelta(days=config.VOICE_PROBE_COMPACT_AFTER_DAYS)
            )
            await self.journal.prune(
                today=today, retention_days=config.VOICE_PROBE_RETENTION_DAYS
            )
        except OSError as exc:
            logger.warning("Voice journal maintenance failed: %s", type(exc).__name__)
            logger.debug("Voice maintenance traceback", exc_info=True)

    @_heartbeat.before_loop
    @_maintenance.before_loop
    async def _before_loops(self) -> None:
        await self.bot.wait_until_ready()

    def _record(self, fact: VoiceFact, guild_id: int | None = None) -> Submission:
        self._sequence += 1
        return self.journal.submit(
            VoiceJournalRecord(
                self._sequence,
                self._boot_id,
                self._now(),
                self._monotonic(),
                fact,
                guild_id,
            )
        )


def normalize_voice_state(
    payload: JsonObject,
    *,
    is_bot: bool | None = None,
    afk_channel_id: int | None = None,
) -> VoiceStateSnapshot:
    """Normalize a raw Discord state without requiring resolved Discord objects."""
    user_id = _id(payload.get("user_id"))
    if user_id is None or "channel_id" not in payload:
        raise ValueError("Missing voice identity")
    member = payload.get("member")
    if is_json_object(member):
        user = member.get("user")
        if is_json_object(user) and isinstance(user.get("bot"), bool):
            is_bot = _flag(user["bot"])
    requested = payload.get("request_to_speak_timestamp")
    session_id = payload.get("session_id")
    channel_id = _id(payload.get("channel_id"))
    requested_to_speak = None
    if "request_to_speak_timestamp" in payload:
        if requested is not None and not isinstance(requested, str):
            raise ValueError("Invalid requested-to-speak timestamp")
        requested_to_speak = requested is not None
    return VoiceStateSnapshot(
        user_id,
        channel_id,
        is_bot,
        self_mute=_flag(payload.get("self_mute")),
        self_deaf=_flag(payload.get("self_deaf")),
        server_mute=_flag(payload.get("mute")),
        server_deaf=_flag(payload.get("deaf")),
        self_stream=_flag(payload.get("self_stream")),
        self_video=_flag(payload.get("self_video")),
        suppress=_flag(payload.get("suppress")),
        requested_to_speak=requested_to_speak,
        requested_to_speak_at=datetime.fromisoformat(requested)
        if isinstance(requested, str)
        else None,
        session_id=session_id if isinstance(session_id, str) else None,
        afk=channel_id == afk_channel_id if afk_channel_id is not None else None,
    )


def _cached_state(
    guild: discord.Guild, user_id: int, state: discord.VoiceState
) -> VoiceStateSnapshot:
    member = guild.get_member(user_id)
    channel = state.channel
    return VoiceStateSnapshot(
        user_id,
        channel.id if channel is not None else None,
        member.bot if member is not None else None,
        self_mute=state.self_mute,
        self_deaf=state.self_deaf,
        server_mute=state.mute,
        server_deaf=state.deaf,
        self_stream=state.self_stream,
        self_video=state.self_video,
        suppress=state.suppress,
        requested_to_speak=state.requested_to_speak_at is not None,
        requested_to_speak_at=state.requested_to_speak_at,
        session_id=state.session_id,
        channel_known=channel is not None,
        afk=channel == guild.afk_channel
        if channel is not None and guild.afk_channel is not None
        else None,
    )


def _id(value: JsonValue) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Invalid Discord ID")
    return int(value)


def _flag(value: JsonValue) -> bool | None:
    return value if isinstance(value, bool) else None


async def setup(bot: commands.Bot) -> None:
    """Register the voice collector through the normal extension lifecycle."""
    await bot.add_cog(VoiceCollectorCog(bot))
