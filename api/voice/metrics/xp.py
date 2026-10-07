"""Exact, display-only voice XP with time-local maximum across contexts.

Only this projection selects a winning context. Presence, companions and the
journal retain their existing semantics. Rates describe visible flags, not
proof of speaking, listening or viewing somebody's stream.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from itertools import pairwise

from api.voice.model import VoiceStateSnapshot, require_aware
from api.voice.read_models import VoiceXpBreakdown
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import RoomInterval, VoiceTimeline

_MICROSECOND = timedelta(microseconds=1)
_MICROSECONDS_PER_HOUR = 3_600_000_000


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceXpPolicy:
    """Apply voice-v3 rates; tuning parameters should get a distinct version.

    Fraction parameters intentionally reject floats. Use Fraction("0.85") or
    Fraction(17, 20), not Fraction(0.85), for exact decimal design values.
    Unknown flags neither earn bonuses nor apply penalties. Unknown occupants
    do not count toward social or large-group thresholds.
    """

    version: str = "voice-v3"
    solo_per_hour: Fraction = Fraction(300)
    social_per_hour: Fraction = Fraction(1200)
    large_group_bonus: Fraction = Fraction(75)
    large_group_minimum: int = 5
    mute_factor: Fraction = Fraction(17, 20)
    suppress_factor: Fraction = Fraction(4, 5)
    deaf_factor: Fraction = Fraction(1, 4)
    stream_bonus: Fraction = Fraction(300)
    video_bonus: Fraction = Fraction(120)
    contribution_cap: Fraction = Fraction(360)

    def __post_init__(self) -> None:
        """Reject ambiguous versions, inexact rates and invalid thresholds."""
        if not self.version.strip():
            raise ValueError("XP policy needs a version")
        _require_group_threshold(self.large_group_minimum)
        rates = (
            self.solo_per_hour,
            self.social_per_hour,
            self.large_group_bonus,
            self.stream_bonus,
            self.video_bonus,
            self.contribution_cap,
        )
        factors = (self.mute_factor, self.suppress_factor, self.deaf_factor)
        for value in (*rates, *factors):
            _require_rate(value)
        if any(value > 1 for value in factors):
            raise ValueError("Audio factors cannot exceed one")

    def rate(self, room: RoomInterval, user_id: int) -> VoiceXpBreakdown:
        """Return the component award for exactly one hour in this room state.

        Five-plus means five known human accounts INCLUDING this user. Known
        bots and unidentified occupants do not count. Peer mute/deaf states do
        not affect the headcount. AFK is an eligibility gate for the recipient.
        """
        if user_id <= 0:
            raise ValueError("User ID must be positive")
        state = next((s for s in room.states if s.user_id == user_id), None)
        if state is None or room.gaps:
            return VoiceXpBreakdown()
        if state.is_bot is not False or state.afk is True:
            return VoiceXpBreakdown()
        if not state.channel_known or state.channel_id != room.channel_id:
            return VoiceXpBreakdown()
        return self._hourly_award(state, len(room.humans))

    def calculate(
        self, timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
    ) -> Fraction:
        """Integrate the maximum complete rate, never sum simultaneous contexts."""
        return self.explain(timeline, user_id, scope).total

    def explain(
        self, timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
    ) -> VoiceXpBreakdown:
        """Return exact components from winning contexts only, without rounding.

        Scope filtering and clipping happen before selection. Equal total rates
        choose the smallest (guild_id, channel_id), making breakdown reproducible.
        Input must be a canonical timeline from build_timeline, including its
        split gap intervals; this does not infer missing coverage from raw facts.
        """
        if user_id <= 0:
            raise ValueError("User ID must be positive")
        intervals = [
            _rated_interval(room, self.rate(room, user_id))
            for room in observed_rooms(timeline, scope)
            if user_id in room.humans
        ]
        return _sum_awards(_winning_intervals(intervals))

    def _hourly_award(
        self, state: VoiceStateSnapshot, human_count: int
    ) -> VoiceXpBreakdown:
        social = human_count >= 2
        base = self.social_per_hour if social else self.solo_per_hour
        large = (
            self.large_group_bonus
            if human_count >= self.large_group_minimum
            else Fraction()
        )
        stream = (
            self.stream_bonus if social and state.self_stream is True else Fraction()
        )
        video = self.video_bonus if social and state.self_video is True else Fraction()
        mute, deaf = self._audio_reductions(state, base + large)
        return VoiceXpBreakdown(
            solo_base=Fraction() if social else base,
            social_base=base if social else Fraction(),
            large_group_bonus=large,
            mute_reduction=mute,
            deaf_reduction=deaf,
            stream_bonus=stream,
            video_bonus=video,
            bonus_cap_reduction=max(Fraction(), stream + video - self.contribution_cap),
        )

    def _audio_reductions(
        self, state: VoiceStateSnapshot, base: Fraction
    ) -> tuple[Fraction, Fraction]:
        mute = (
            self.mute_factor
            if state.self_mute is True or state.server_mute is True
            else Fraction(1)
        )
        stage = self.suppress_factor if state.suppress is True else Fraction(1)
        deaf = (
            self.deaf_factor
            if state.self_deaf is True or state.server_deaf is True
            else Fraction(1)
        )
        factor = min(mute, stage, deaf)
        reduction = base * (1 - factor)
        # Stage suppression belongs to mute; equal factors attribute to deaf.
        if factor == deaf:
            return Fraction(), reduction
        return reduction, Fraction()


@dataclass(frozen=True, slots=True)
class _RatedInterval:
    started_at: datetime
    ended_at: datetime
    guild_id: int
    channel_id: int
    award: VoiceXpBreakdown


def _rated_interval(room: RoomInterval, award: VoiceXpBreakdown) -> _RatedInterval:
    require_aware(room.started_at)
    require_aware(room.ended_at)
    start = room.started_at.astimezone(UTC)
    end = room.ended_at.astimezone(UTC)
    if end <= start:
        raise ValueError("XP intervals must have positive elapsed duration")
    return _RatedInterval(start, end, room.guild_id, room.channel_id, award)


def _rank(interval: _RatedInterval) -> tuple[Fraction, int, int]:
    return -interval.award.total, interval.guild_id, interval.channel_id


def _winning_intervals(
    intervals: Sequence[_RatedInterval],
) -> Iterator[tuple[timedelta, VoiceXpBreakdown]]:
    # Sweep boundaries rather than seconds. Removing and adding at one boundary
    # happens before awarding [boundary, next_boundary), so touching intervals
    # never overlap. Active contexts per user are normally few.
    changes: dict[datetime, list[tuple[int, bool]]] = {}
    for index, interval in enumerate(intervals):
        changes.setdefault(interval.started_at, []).append((index, True))
        changes.setdefault(interval.ended_at, []).append((index, False))
    active: dict[int, _RatedInterval] = {}
    for start, end in pairwise(sorted(changes)):
        for index, entering in changes[start]:
            if entering:
                active[index] = intervals[index]
            else:
                active.pop(index)
        if active:
            # A sole context needs no Fraction-based ranking.
            winner = (
                next(iter(active.values()))
                if len(active) == 1
                else min(active.values(), key=_rank)
            )
            yield end - start, winner.award


def _sum_awards(
    intervals: Iterator[tuple[timedelta, VoiceXpBreakdown]],
) -> VoiceXpBreakdown:
    # Combine exact microseconds at equal rates before multiplying fractions.
    # Splitting an observation into more checkpoints must not add arithmetic work.
    durations: dict[VoiceXpBreakdown, int] = {}
    for duration, rate in intervals:
        durations[rate] = durations.get(rate, 0) + duration // _MICROSECOND
    solo = social = large = stream = video = cap = Fraction()
    mute = deaf = Fraction()
    for rate, microseconds in durations.items():
        hours = Fraction(microseconds, _MICROSECONDS_PER_HOUR)
        solo += rate.solo_base * hours
        social += rate.social_base * hours
        large += rate.large_group_bonus * hours
        mute += rate.mute_reduction * hours
        deaf += rate.deaf_reduction * hours
        stream += rate.stream_bonus * hours
        video += rate.video_bonus * hours
        cap += rate.bonus_cap_reduction * hours
    return VoiceXpBreakdown(
        solo_base=solo,
        social_base=social,
        large_group_bonus=large,
        stream_bonus=stream,
        video_bonus=video,
        bonus_cap_reduction=cap,
        mute_reduction=mute,
        deaf_reduction=deaf,
    )


def _require_group_threshold(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Large-group threshold must be an integer")
    if value < 3:
        raise ValueError("Large groups must have at least three humans")


def _require_rate(value: object) -> None:
    if not isinstance(value, Fraction):
        raise TypeError("XP rates and factors must be Fraction values")
    if value < 0:
        raise ValueError("XP rates and factors must be nonnegative")
