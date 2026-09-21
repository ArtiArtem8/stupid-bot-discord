"""Immutable facts shared by collection, persistence and timeline reconstruction."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class GapReason(StrEnum):
    """Reasons observation cannot establish continuous presence."""

    GATEWAY_DISCONNECT = "gateway_disconnect"
    PROCESS_RESTART = "process_restart"
    WRITER_OVERFLOW = "writer_overflow"
    WRITE_FAILURE = "write_failure"
    CLOCK_DISCONTINUITY = "clock_discontinuity"
    UNKNOWN = "unknown"


def require_aware(moment: datetime) -> None:
    """Reject wall times that cannot be placed on the UTC timeline."""
    if moment.utcoffset() is None:
        raise ValueError("Voice timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class VoiceStateSnapshot:
    """One user's observed voice state; None flags mean unavailable knowledge.

    A known null channel is a leave. An unresolved cache channel instead has
    channel_known=False. Humans, bots and unresolved members share this model.
    requested_to_speak_at is a timestamp when available; None does not invent a
    timestamp for legacy raised-hand booleans.
    """

    user_id: int
    channel_id: int | None
    is_bot: bool | None = None
    self_mute: bool | None = None
    self_deaf: bool | None = None
    server_mute: bool | None = None
    server_deaf: bool | None = None
    self_stream: bool | None = None
    self_video: bool | None = None
    suppress: bool | None = None
    requested_to_speak_at: datetime | None = None
    session_id: str | None = None
    channel_known: bool = True
    afk: bool | None = None

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        if self.user_id <= 0 or (self.channel_id is not None and self.channel_id <= 0):
            raise ValueError("Discord IDs must be positive")
        if self.requested_to_speak_at is not None:
            require_aware(self.requested_to_speak_at)


@dataclass(frozen=True, slots=True)
class VoiceObservation:
    """A state received at the record's observation time, without classification."""

    state: VoiceStateSnapshot


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    """Full guild state from Discord, or an explicitly local checkpoint.

    Only authoritative snapshots establish coverage. Unknown cache channel IDs
    must make the snapshot non-authoritative rather than imply a leave.
    """

    states: tuple[VoiceStateSnapshot, ...]
    authoritative: bool = True

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        if len({state.user_id for state in self.states}) != len(self.states):
            raise ValueError("A snapshot cannot repeat a user")
        if self.authoritative and any(not state.channel_known for state in self.states):
            raise ValueError("An authoritative snapshot needs known channels")


@dataclass(frozen=True, slots=True)
class ObservationGap:
    """Unobserved half-open window; no end means awaiting a full snapshot.

    A global gap applies independently to every guild. known_bounds=False means
    the start is a conservative last-observation bound, not the failure instant.
    """

    started_at: datetime
    ended_at: datetime | None
    reason: GapReason
    guild_id: int | None = None
    known_bounds: bool = True

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        require_aware(self.started_at)
        if self.ended_at is not None:
            require_aware(self.ended_at)
            if self.ended_at < self.started_at:
                raise ValueError("Gap ends before it starts")


@dataclass(frozen=True, slots=True)
class VoiceCheckpoint:
    """Liveness evidence only; never confirms a user's old state after a gap."""


@dataclass(frozen=True, slots=True)
class VoiceLifecycle:
    """A process or guild boundary; stopped guilds require a new snapshot."""

    stopped: bool = False


type VoiceFact = (
    VoiceObservation | VoiceSnapshot | ObservationGap | VoiceCheckpoint | VoiceLifecycle
)


@dataclass(frozen=True, slots=True)
class VoiceJournalRecord:
    """One ordered fact; sequence is process-wide within a unique boot ID."""

    sequence: int
    boot_id: str
    observed_at: datetime
    monotonic: float
    fact: VoiceFact
    guild_id: int | None = None

    def __post_init__(self) -> None:
        """Validate the value object invariants at construction."""
        require_aware(self.observed_at)
        if not isfinite(self.monotonic):
            raise ValueError("Monotonic time must be finite")
        if not self.boot_id or self.sequence < 0:
            raise ValueError("Records need a boot ID and a nonnegative sequence")
        if (
            isinstance(self.fact, (VoiceObservation, VoiceSnapshot))
            and self.guild_id is None
        ):
            raise ValueError("Voice states need a guild")
        if (
            isinstance(self.fact, ObservationGap)
            and self.fact.guild_id != self.guild_id
        ):
            raise ValueError("Gap and record guild must agree")
