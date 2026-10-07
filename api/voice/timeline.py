"""Pure journal replay into room intervals and explicit observation gaps."""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import pairwise

from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
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
class ObservationInterval:
    """Half-open confirmed guild coverage, including time with no occupied room."""

    guild_id: int
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class VoiceTimeline:
    """Rebuildable analytical history with gaps retained even for empty rooms."""

    rooms: tuple[RoomInterval, ...]
    gaps: tuple[ObservationGap, ...]
    coverage: tuple[ObservationInterval, ...]


def build_timeline(records: Iterable[VoiceJournalRecord]) -> VoiceTimeline:
    """Replay complete facts once, ending at the last recorded observation.

    Boot groups are ordered by their earliest timestamp; within a boot sequence
    wins over wall-clock order. Readers should include the preceding snapshot
    and session markers. Missing initial coverage is never invented.
    """
    return VoiceReplayState(records).snapshot()


class VoiceReplayState:
    """Retain replay state, without raw history or persistence concerns.

    The owner serializes mutation and snapshots. Unsupported append ordering
    returns False without mutation; recover by constructing from full history.
    Snapshots finish detached containers and never finalize the live tail.
    """

    def __init__(self, records: Iterable[VoiceJournalRecord]) -> None:
        ordered = _ordered_records(records)
        guilds = sorted({r.guild_id for r in ordered if r.guild_id is not None})
        self._guilds = {guild: _GuildReplay(guild) for guild in guilds}
        for record in ordered:
            self._apply(record)
        self._last = ordered[-1] if ordered else None
        self._boot_start = min(
            (
                r.observed_at
                for r in ordered
                if self._last and r.boot_id == self._last.boot_id
            ),
            default=None,
        )

    def apply_many(self, records: Sequence[VoiceJournalRecord]) -> bool:
        """Apply an ordered continuation, or require a canonical rebuild."""
        last = self._last
        for record in records:
            if not self._safe_after(last, record):
                return False
            last = record
        for record in records:
            self._apply(record)
        self._last = last
        return True

    def _safe_after(
        self, previous: VoiceJournalRecord | None, record: VoiceJournalRecord
    ) -> bool:
        return (
            previous is not None
            and record.boot_id == previous.boot_id
            and record.sequence > previous.sequence
            and self._boot_start is not None
            and record.observed_at >= self._boot_start
            and not isinstance(record.fact, ObservationGap)
            and (record.guild_id is None or record.guild_id in self._guilds)
        )

    def _apply(self, record: VoiceJournalRecord) -> None:
        for guild_id, replay in self._guilds.items():
            if record.guild_id in (None, guild_id):
                replay.apply(record)

    def snapshot(self, guild_id: int | None = None) -> VoiceTimeline:
        """Return immutable intervals at the recorded horizon, optionally scoped."""
        rooms: list[RoomInterval] = []
        gaps: list[ObservationGap] = []
        coverage: list[ObservationInterval] = []
        for guild, replay in self._guilds.items():
            if guild_id is not None and guild != guild_id:
                continue
            detached = replace(
                replay,
                rooms=replay.rooms.copy(),
                gaps=replay.gaps.copy(),
                pending=replay.pending.copy(),
                last_room=replay.last_room.copy(),
            )
            detached.finish()
            gaps.extend(detached.gaps)
            coverage.extend(detached.observed_coverage())
            rooms.extend(_split_rooms(detached.rooms, detached.gaps))
        return VoiceTimeline(
            tuple(
                sorted(rooms, key=lambda r: (r.started_at, r.guild_id, r.channel_id))
            ),
            tuple(sorted(gaps, key=lambda g: (g.started_at, g.guild_id or 0))),
            tuple(sorted(coverage, key=lambda c: (c.started_at, c.guild_id))),
        )


def _split_rooms(
    rooms: list[RoomInterval], gaps: list[ObservationGap]
) -> Iterable[RoomInterval]:
    ordered = sorted(enumerate(gaps), key=lambda item: item[1].started_at)
    starts = [gap.started_at for _, gap in ordered]
    max_ends: list[datetime] = []
    end = datetime.min.replace(tzinfo=UTC)
    for _, gap in ordered:
        end = max(end, gap.ended_at or datetime.max.replace(tzinfo=UTC))
        max_ends.append(end)
    for room in rooms:
        first = bisect_right(max_ends, room.started_at)
        last = bisect_left(starts, room.ended_at)
        # Restore source order: gap metadata ordering is observable to readers.
        candidates = [gap for _, gap in sorted(ordered[first:last])]
        yield from _split_at_gaps(room, candidates)


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
    emitted_through: datetime | None = None
    last_room: dict[int, int] = field(default_factory=dict)
    history_started_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    boot_started_at: datetime | None = None

    def apply(self, record: VoiceJournalRecord) -> None:
        moment = record.observed_at
        checkpoint = isinstance(record.fact, VoiceCheckpoint)
        if self.history_started_at is None:
            self.history_started_at = moment
        if self.cursor is not None:
            moment = max(moment, self.cursor)
        if self.emitted_through is None:
            self.emitted_through = moment
        elif not checkpoint:
            # Checkpoints advance clock/gap detection but cannot change occupants.
            self._emit(self.emitted_through, moment)
            self.emitted_through = moment
        self._boundaries(record)
        if not checkpoint:
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
            ordered = tuple(sorted(states, key=lambda s: s.user_id))
            previous_index = self.last_room.get(channel_id)
            if previous_index is not None:
                previous = self.rooms[previous_index]
                if previous.ended_at == start and previous.states == ordered:
                    self.rooms[previous_index] = replace(previous, ended_at=end)
                    continue
            self.last_room[channel_id] = len(self.rooms)
            self.rooms.append(
                RoomInterval(self.guild_id, channel_id, start, end, ordered)
            )

    def finish(self) -> None:
        if self.emitted_through is not None and self.cursor is not None:
            self._emit(self.emitted_through, self.cursor)
        self.gaps.extend(self.pending)

    def observed_coverage(self) -> list[ObservationInterval]:
        # Subtract finalized gaps, including retrospective invalidation, from
        # the recorded horizon. Room occupancy does not establish coverage.
        cursor, end = self.history_started_at, self.cursor
        if cursor is None or end is None:
            return []
        result: list[ObservationInterval] = []
        for gap in sorted(self.gaps, key=lambda gap: gap.started_at):
            stop = min(gap.started_at, end)
            if cursor < stop:
                result.append(ObservationInterval(self.guild_id, cursor, stop))
            cursor = max(cursor, gap.ended_at or end)
        if cursor < end:
            result.append(ObservationInterval(self.guild_id, cursor, end))
        return result


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
    if not relevant:
        return [room]
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
    # discord.py can retain an old transport session_id in its voice cache.
    # That diagnostic field cannot prove a missed presence or flag change.
    previous = (
        before.self_mute,
        before.self_deaf,
        before.server_mute,
        before.server_deaf,
        before.self_stream,
        before.self_video,
        before.suppress,
        before.requested_to_speak,
        before.requested_to_speak_at,
    )
    current = (
        after.self_mute,
        after.self_deaf,
        after.server_mute,
        after.server_deaf,
        after.self_stream,
        after.self_video,
        after.suppress,
        after.requested_to_speak,
        after.requested_to_speak_at,
    )
    return any(
        old != new
        for old, new in zip(previous, current, strict=True)
        if old is not None and new is not None
    )
