"""Pure journal replay into room intervals and explicit observation gaps."""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise

from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceJournalRecord,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)


@dataclass(frozen=True, slots=True)
class RoomInterval:
    """Half-open constant room state; gaps prohibit crediting this interval.

    Unknown bot identity is neither human nor bot. States remain available for
    future flag metrics without a second journal replay.
    """

    guild_id: int
    channel_id: int
    started_at: datetime
    ended_at: datetime
    states: tuple[VoiceStateSnapshot, ...]
    gaps: tuple[ObservationGap, ...] = ()

    @property
    def humans(self) -> tuple[int, ...]:
        """Return positively identified humans in stable user-ID order."""
        return tuple(state.user_id for state in self.states if state.is_bot is False)

    @property
    def bots(self) -> tuple[int, ...]:
        """Return positively identified bots."""
        return tuple(state.user_id for state in self.states if state.is_bot is True)

    @property
    def seconds(self) -> float:
        """Return interval duration in seconds."""
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class VoiceTimeline:
    """Rebuildable analytical history with gaps retained even for empty rooms."""

    rooms: tuple[RoomInterval, ...]
    gaps: tuple[ObservationGap, ...]


def build_timeline(records: Iterable[VoiceJournalRecord]) -> VoiceTimeline:
    """Replay complete facts once, ending at the last recorded observation.

    Boot groups are ordered by their earliest timestamp; within a boot sequence
    wins over wall-clock order. Readers should include the preceding snapshot
    and session markers. Missing initial coverage is never invented.
    """
    ordered = _ordered_records(records)
    guild_ids = sorted({r.guild_id for r in ordered if r.guild_id is not None})
    rooms: list[RoomInterval] = []
    gaps: list[ObservationGap] = []
    for guild_id in guild_ids:
        replay = _GuildReplay(guild_id)
        for record in ordered:
            if record.guild_id in (None, guild_id):
                replay.apply(record)
        replay.finish()
        gaps.extend(replay.gaps)
        for room in replay.rooms:
            rooms.extend(_split_at_gaps(room, replay.gaps))
    return VoiceTimeline(
        tuple(sorted(rooms, key=lambda r: (r.started_at, r.guild_id, r.channel_id))),
        tuple(sorted(gaps, key=lambda g: (g.started_at, g.guild_id or 0))),
    )


def _ordered_records(records: Iterable[VoiceJournalRecord]) -> list[VoiceJournalRecord]:
    boots: dict[str, list[VoiceJournalRecord]] = {}
    for record in records:
        boots.setdefault(record.boot_id, []).append(record)
    ordered: list[VoiceJournalRecord] = []
    for batch in sorted(boots.values(), key=lambda b: min(r.observed_at for r in b)):
        # A writer loss marker can reuse a failed sequence. Apply it after that
        # fact, so a partially persisted snapshot cannot erase its uncertainty.
        ordered.extend(
            sorted(
                batch, key=lambda r: (r.sequence, isinstance(r.fact, ObservationGap))
            )
        )
    return ordered


@dataclass(slots=True)
class _GuildReplay:
    guild_id: int
    states: dict[int, VoiceStateSnapshot] = field(default_factory=dict)
    rooms: list[RoomInterval] = field(default_factory=list)
    gaps: list[ObservationGap] = field(default_factory=list)
    pending: list[ObservationGap] = field(default_factory=list)
    previous: VoiceJournalRecord | None = None
    cursor: datetime | None = None
    last_snapshot_at: datetime | None = None
    boot_started_at: datetime | None = None

    def apply(self, record: VoiceJournalRecord) -> None:
        moment = record.observed_at
        if self.cursor is not None:
            moment = max(moment, self.cursor)
            self._emit(self.cursor, moment)
        self._boundaries(record)
        self._apply_fact(record, moment)
        self.previous = record
        self.cursor = moment

    def _boundaries(self, record: VoiceJournalRecord) -> None:
        previous = self.previous
        if previous is None:
            self.boot_started_at = record.observed_at
            self.pending.append(
                ObservationGap(
                    record.observed_at,
                    None,
                    GapReason.UNKNOWN,
                    self.guild_id,
                    known_bounds=False,
                )
            )
            return
        if record.boot_id != previous.boot_id:
            self.boot_started_at = record.observed_at
            self.pending.append(
                ObservationGap(
                    previous.observed_at,
                    None,
                    GapReason.PROCESS_RESTART,
                    self.guild_id,
                    False,
                )
            )
        elif _clock_changed(previous, record):
            self.pending.append(
                ObservationGap(
                    min(previous.observed_at, record.observed_at),
                    None,
                    GapReason.CLOCK_DISCONTINUITY,
                    self.guild_id,
                    False,
                )
            )

    def _apply_fact(self, record: VoiceJournalRecord, moment: datetime) -> None:
        fact = record.fact
        if isinstance(fact, ObservationGap):
            self._add_gap(fact, record)
        elif isinstance(fact, VoiceSnapshot) and fact.authoritative:
            self._replace_snapshot(fact, moment)
            # A wall clock that moved backwards cannot confirm a future cursor.
            if record.observed_at == moment:
                self._close_gaps(moment)
        elif isinstance(fact, VoiceObservation):
            state = fact.state
            if state.channel_known and state.channel_id is None:
                self.states.pop(state.user_id, None)
            else:
                self.states[state.user_id] = state
        elif isinstance(fact, VoiceLifecycle) and fact.stopped:
            self.pending.append(
                ObservationGap(moment, None, GapReason.UNKNOWN, self.guild_id)
            )
            self.states.clear()

    def _replace_snapshot(self, snapshot: VoiceSnapshot, moment: datetime) -> None:
        actual = {
            state.user_id: state
            for state in snapshot.states
            if state.channel_id is not None
        }
        if (
            not self.pending
            and self.last_snapshot_at is not None
            and _snapshot_disagrees(self.states, actual)
        ):
            # A missing change could have happened anywhere since the preceding
            # authoritative snapshot, not necessarily at this snapshot's time.
            self.gaps.append(
                ObservationGap(
                    self.last_snapshot_at,
                    moment,
                    GapReason.UNKNOWN,
                    self.guild_id,
                    known_bounds=False,
                )
            )
        self.states = actual
        self.last_snapshot_at = moment

    def _add_gap(self, gap: ObservationGap, record: VoiceJournalRecord) -> None:
        start = gap.started_at
        if (
            not gap.known_bounds
            and start == record.observed_at
            and self.previous is not None
        ):
            # Legacy drops/drift have no loss timestamp. A preceding heartbeat
            # may itself follow the lost event, so it is not a safe lower bound.
            start = min(start, self.boot_started_at or self.previous.observed_at)
        scoped = replace(gap, guild_id=self.guild_id, started_at=start)
        if scoped.ended_at is None:
            self.pending.append(scoped)
        else:
            self.gaps.append(scoped)
            # A bounded report establishes no coverage after its end either.
            self.pending.append(
                replace(scoped, started_at=scoped.ended_at, ended_at=None)
            )

    def _close_gaps(self, moment: datetime) -> None:
        for gap in self.pending:
            if gap.started_at < moment:
                self.gaps.append(replace(gap, ended_at=moment))
        self.pending.clear()

    def _emit(self, start: datetime, end: datetime) -> None:
        if end <= start:
            return
        channels: dict[int, list[VoiceStateSnapshot]] = {}
        for state in self.states.values():
            if state.channel_known and state.channel_id is not None:
                channels.setdefault(state.channel_id, []).append(state)
        for channel_id, states in channels.items():
            self.rooms.append(
                RoomInterval(
                    self.guild_id,
                    channel_id,
                    start,
                    end,
                    tuple(sorted(states, key=lambda s: s.user_id)),
                )
            )

    def finish(self) -> None:
        self.gaps.extend(self.pending)


def _clock_changed(previous: VoiceJournalRecord, current: VoiceJournalRecord) -> bool:
    elapsed = current.monotonic - previous.monotonic
    wall = (current.observed_at - previous.observed_at).total_seconds()
    return elapsed < 0 or wall < 0 or abs(wall - elapsed) > 5.0


def _split_at_gaps(
    room: RoomInterval, gaps: list[ObservationGap]
) -> list[RoomInterval]:
    relevant = [
        gap
        for gap in gaps
        if gap.started_at < room.ended_at
        and (gap.ended_at is None or gap.ended_at > room.started_at)
    ]
    boundaries = {room.started_at, room.ended_at}
    for gap in relevant:
        boundaries.add(max(room.started_at, gap.started_at))
        boundaries.add(min(room.ended_at, gap.ended_at or room.ended_at))
    ordered = sorted(boundaries)
    return [
        replace(
            room,
            started_at=start,
            ended_at=end,
            gaps=tuple(
                g
                for g in relevant
                if g.started_at < end and (g.ended_at is None or g.ended_at > start)
            ),
        )
        for start, end in pairwise(ordered)
    ]


def _snapshot_disagrees(
    expected: dict[int, VoiceStateSnapshot], actual: dict[int, VoiceStateSnapshot]
) -> bool:
    if expected.keys() != actual.keys():
        return True
    return any(
        _known_state_changed(expected[user], state) for user, state in actual.items()
    )


def _known_state_changed(before: VoiceStateSnapshot, after: VoiceStateSnapshot) -> bool:
    if before.channel_known and before.channel_id != after.channel_id:
        return True
    previous = (
        before.self_mute,
        before.self_deaf,
        before.server_mute,
        before.server_deaf,
        before.self_stream,
        before.self_video,
        before.suppress,
        before.requested_to_speak_at,
        before.session_id,
    )
    current = (
        after.self_mute,
        after.self_deaf,
        after.server_mute,
        after.server_deaf,
        after.self_stream,
        after.self_video,
        after.suppress,
        after.requested_to_speak_at,
        after.session_id,
    )
    return any(
        old != new
        for old, new in zip(previous, current, strict=True)
        if old is not None and new is not None
    )
