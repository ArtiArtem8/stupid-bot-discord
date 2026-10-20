"""Read process-owned runtime state without network, storage or rendering work."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from discord.ext import commands

from stupidbot.features.voice.journal import JournalCounts
from stupidbot.features.voice.runtime import AnalyticsStatus
from stupidbot.storage.database import DatabaseHealth


@dataclass(frozen=True, slots=True)
class TaskHealth:
    """One expected loop's current state, without restarting failed work."""

    name: str
    running: bool
    failed: bool = False

    def summary(self) -> str:
        state = "failed" if self.failed else "stopped"
        if self.running:
            state = "running"
        return f"{self.name}:{state}"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Expose feature readiness and expected loops without exposing mutable owners."""

    ready: bool
    tasks: tuple[TaskHealth, ...] = ()


@runtime_checkable
class HealthProvider(Protocol):
    """Let diagnostics sample loaded cogs without importing their entry modules."""

    @property
    def runtime_health(self) -> ComponentHealth:
        """Read local state only; do not initiate I/O or recovery."""
        ...


def component_health(cogs: Mapping[str, commands.Cog]) -> dict[str, ComponentHealth]:
    """Sample the diagnostic capabilities present in the currently loaded cogs."""
    return {
        name: cog.runtime_health
        for name, cog in cogs.items()
        if isinstance(cog, HealthProvider)
    }


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """A bounded local diagnostic; readiness does not prove external availability."""

    ready: bool
    latency_ms: float | None
    uptime_seconds: float | None
    checkpoint_us: int | None
    database: DatabaseHealth
    tasks: tuple[TaskHealth, ...]
    voice_enabled: bool
    voice_writer: bool
    voice_pending: int
    voice_counts: JournalCounts | None
    voice_last_commit_at: datetime | None
    analytics: AnalyticsStatus
    music_ready: bool
    renderer_ready: bool

    @property
    def degraded(self) -> bool:
        """Flag unavailable components, independently of historical failure totals."""
        checks = (
            self.ready,
            not self.database.closing,
            all(task.running for task in self.tasks),
            self.analytics == "ready",
            not self.voice_enabled or self.voice_writer,
        )
        return not all(checks)

    @property
    def failures(self) -> tuple[int, int, int, int]:
        """Return counters for detecting new failures between periodic reports."""
        counts = self.voice_counts
        return (
            self.database.busy,
            self.database.checkout_timeouts,
            counts.failed if counts is not None else 0,
            counts.rejected if counts is not None else 0,
        )

    def lines(self) -> tuple[str, ...]:
        """Format only fixed technical keys and bounded local values."""
        checkpoint = (
            datetime.fromtimestamp(self.checkpoint_us / 1_000_000, UTC).isoformat()
            if self.checkpoint_us is not None
            else "unavailable"
        )
        latency = f"{self.latency_ms:.0f}" if self.latency_ms is not None else "unknown"
        uptime = (
            f"{self.uptime_seconds:.0f}"
            if self.uptime_seconds is not None
            else "unavailable"
        )
        db = self.database
        return (
            f"discord_ready={self.ready} latency_ms={latency} uptime_s={uptime}",
            f"checkpoint_utc={checkpoint}",
            f"database_closing={db.closing} admitted={db.admitted} "
            f"busy_total={db.busy} checkout_timeouts_total={db.checkout_timeouts}",
            "tasks=" + ",".join(task.summary() for task in self.tasks),
            self._voice_line(),
            f"analytics={self.analytics} "
            f"lavalink_ready={self.music_ready} renderer_ready={self.renderer_ready}",
        )

    def _voice_line(self) -> str:
        if not self.voice_enabled:
            return "voice_collection=disabled"
        counts = self.voice_counts
        if counts is None:
            return "voice_collection=unavailable"
        committed = (
            self.voice_last_commit_at.isoformat()
            if self.voice_last_commit_at is not None
            else "none"
        )
        return (
            f"voice_writer={self.voice_writer} pending={self.voice_pending} "
            f"accepted={counts.accepted} persisted={counts.persisted} "
            f"failed={counts.failed} rejected={counts.rejected} "
            f"last_commit_utc={committed}"
        )
