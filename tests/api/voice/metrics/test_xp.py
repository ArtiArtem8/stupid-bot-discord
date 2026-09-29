"""Hand-computable rate and interval algebra regressions."""

import unittest
from dataclasses import replace
from datetime import timedelta, timezone
from fractions import Fraction
from itertools import product

from api.voice.metrics.xp import VoiceXpPolicy
from api.voice.model import GapReason, ObservationGap, VoiceStateSnapshot
from api.voice.read_models import VoiceXpBreakdown
from api.voice.scope import TimeRange, VoiceScope
from api.voice.timeline import RoomInterval, VoiceTimeline
from tests.api.voice.examples import at, example, human


def room(
    *,
    start: int = 0,
    end: int = 3600,
    guild: int = 1,
    channel: int = 10,
    humans: int = 2,
    state: VoiceStateSnapshot | None = None,
    extras: tuple[VoiceStateSnapshot, ...] = (),
    gaps: tuple[ObservationGap, ...] = (),
) -> RoomInterval:
    target = human(1, channel) if state is None else state
    states = (target, *(human(user, channel) for user in range(2, humans + 1)), *extras)
    return RoomInterval(guild, channel, at(start), at(end), states, gaps)


def timeline(*rooms: RoomInterval) -> VoiceTimeline:
    # The metrics accept canonical room slices; replay/coverage has its own suite.
    return VoiceTimeline(rooms, (), ())


class TestXpRates(unittest.TestCase):
    def test_context_and_flag_rate_table(self) -> None:
        cases = (
            (room(humans=1), Fraction(300)),
            (room(), Fraction(1200)),
            (room(humans=4), Fraction(1200)),
            (room(humans=5), Fraction(1275)),
            (room(humans=10), Fraction(1275)),
            (room(state=replace(human(), self_mute=True)), Fraction(1020)),
            (room(state=replace(human(), server_mute=True)), Fraction(1020)),
            (room(state=replace(human(), suppress=True)), Fraction(960)),
            (room(state=replace(human(), self_deaf=True)), Fraction(300)),
            (room(state=replace(human(), server_deaf=True)), Fraction(300)),
            (room(state=replace(human(), self_stream=True)), Fraction(1500)),
            (room(state=replace(human(), self_video=True)), Fraction(1320)),
            (
                room(state=replace(human(), self_stream=True, self_video=True)),
                Fraction(1560),
            ),
            (
                room(
                    humans=5, state=replace(human(), self_stream=True, self_video=True)
                ),
                Fraction(1635),
            ),
            (room(humans=5, state=replace(human(), self_deaf=True)), Fraction(1275, 4)),
        )
        policy = VoiceXpPolicy()
        for sample, expected in cases:
            with self.subTest(sample=sample):
                self.assertEqual(policy.rate(sample, 1).total, expected)
                self.assertEqual(policy.calculate(timeline(sample), 1), expected)

    def test_audio_uses_only_the_strongest_restriction(self) -> None:
        state = replace(
            human(), self_mute=True, server_mute=True, suppress=True, self_deaf=True
        )
        result = VoiceXpPolicy().rate(room(state=state), 1)
        self.assertEqual(result.audio_reduction, 900)
        self.assertEqual(result.total, 300)

    def test_mute_and_suppress_choose_suppress(self) -> None:
        sample = room(state=replace(human(), self_mute=True, suppress=True))
        self.assertEqual(VoiceXpPolicy().rate(sample, 1).total, 960)

    def test_stream_bonus_is_added_after_audio_reduction(self) -> None:
        state = replace(human(), self_deaf=True, self_stream=True)
        self.assertEqual(VoiceXpPolicy().rate(room(state=state), 1).total, 600)

    def test_large_group_bonus_is_audio_adjusted(self) -> None:
        sample = room(humans=5, state=replace(human(), self_mute=True))
        self.assertEqual(VoiceXpPolicy().rate(sample, 1).total, Fraction(4335, 4))

    def test_solo_cannot_earn_stream_or_video_bonus(self) -> None:
        state = replace(human(), self_stream=True, self_video=True)
        rate = VoiceXpPolicy().rate(room(humans=1, state=state), 1)
        self.assertEqual(rate.total, 300)
        self.assertEqual((rate.stream_bonus, rate.video_bonus), (0, 0))

    def test_bots_and_unknowns_do_not_unlock_social_or_large_groups(self) -> None:
        extras = (VoiceStateSnapshot(90, 10, True), VoiceStateSnapshot(91, 10))
        policy = VoiceXpPolicy()
        self.assertEqual(policy.rate(room(humans=1, extras=extras), 1).total, 300)
        self.assertEqual(policy.rate(room(humans=4, extras=extras), 1).total, 1200)

    def test_nonhuman_recipient_and_known_afk_earn_zero(self) -> None:
        for state in (
            VoiceStateSnapshot(1, 10, True),
            VoiceStateSnapshot(1, 10),
            replace(human(), afk=True, self_stream=True, self_video=True),
            replace(human(), channel_id=None),
            replace(human(), channel_known=False),
        ):
            with self.subTest(state=state):
                self.assertEqual(VoiceXpPolicy().rate(room(state=state), 1).total, 0)

    def test_unknown_flags_are_neutral(self) -> None:
        self.assertEqual(VoiceXpPolicy().rate(room(), 1).total, 1200)
        explicit = replace(
            human(),
            afk=False,
            self_mute=False,
            self_deaf=False,
            server_mute=False,
            server_deaf=False,
            suppress=False,
            self_stream=False,
            self_video=False,
        )
        self.assertEqual(VoiceXpPolicy().rate(room(state=explicit), 1).total, 1200)

    def test_gap_blocks_even_the_largest_rate(self) -> None:
        gap = ObservationGap(at(0), at(3600), GapReason.UNKNOWN, 1)
        sample = room(humans=5, state=replace(human(), self_stream=True), gaps=(gap,))
        self.assertEqual(VoiceXpPolicy().rate(sample, 1).total, 0)
        self.assertEqual(VoiceXpPolicy().calculate(timeline(sample), 1), 0)

    def test_breakdown_exposes_cap_without_double_counting(self) -> None:
        sample = room(
            humans=5,
            state=replace(human(), self_mute=True, self_stream=True, self_video=True),
        )
        result = VoiceXpPolicy().explain(timeline(sample), 1)
        self.assertEqual(result.social_base, 1200)
        self.assertEqual(result.large_group_bonus, 75)
        self.assertEqual(result.audio_reduction, Fraction(765, 4))
        self.assertEqual(result.stream_bonus, 300)
        self.assertEqual(result.video_bonus, 120)
        self.assertEqual(result.bonus_cap_reduction, 60)
        self.assertEqual(result.total, Fraction(5775, 4))

    def test_policy_parameters_are_explicit_and_checked(self) -> None:
        tuned = VoiceXpPolicy(version="experiment", stream_bonus=Fraction(400))
        self.assertEqual(tuned.version, "experiment")
        self.assertEqual(
            tuned.rate(room(state=replace(human(), self_stream=True)), 1).total,
            Fraction(1560),
        )
        with self.assertRaises(ValueError):
            VoiceXpPolicy(mute_factor=Fraction(2))
        with self.assertRaises(ValueError):
            VoiceXpPolicy(solo_per_hour=Fraction(-1))
        with self.assertRaises(ValueError):
            VoiceXpPolicy(large_group_minimum=2)
        with self.assertRaises(ValueError):
            VoiceXpPolicy(version=" ")

    def test_invalid_user_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VoiceXpPolicy().calculate(timeline(), 0)


class TestXpIntegration(unittest.TestCase):
    def test_original_hand_computable_history(self) -> None:
        sample = example()
        original = sample
        policy = VoiceXpPolicy()
        self.assertEqual(policy.calculate(sample, 1), 325)
        self.assertEqual(policy.calculate(sample, 2), 200)
        self.assertEqual(policy.calculate(sample, 3), 200)
        self.assertEqual(sample, original)
        self.assertEqual(policy.calculate(sample, 1), 325)

    def test_empty_or_absent_user_has_zero_award(self) -> None:
        policy = VoiceXpPolicy()
        self.assertEqual(policy.explain(timeline(), 1), VoiceXpBreakdown())
        self.assertEqual(policy.calculate(timeline(room()), 99), 0)

    def test_partial_overlap_uses_maximum_at_each_time_not_total_max(self) -> None:
        a = room(start=0, end=3600, guild=1)
        b = room(
            start=1800, end=5400, guild=2, state=replace(human(), self_stream=True)
        )
        sample = timeline(a, b)
        policy = VoiceXpPolicy()
        self.assertEqual(policy.calculate(sample, 1), 2100)
        self.assertEqual(policy.calculate(sample, 1, VoiceScope(1)), 1200)
        self.assertEqual(policy.calculate(sample, 1, VoiceScope(2)), 1500)
        detail = policy.explain(sample, 1)
        self.assertEqual((detail.social_base, detail.stream_bonus), (1800, 300))

    def test_winner_can_change_between_contexts(self) -> None:
        sample = timeline(
            room(start=0, end=1800, guild=1, state=replace(human(), self_stream=True)),
            room(start=1800, end=3600, guild=1, state=replace(human(), self_deaf=True)),
            room(start=0, end=3600, guild=2),
        )
        self.assertEqual(VoiceXpPolicy().calculate(sample, 1), 1350)

    def test_never_combine_normal_audio_with_stream_from_another_context(self) -> None:
        a = room(guild=1)
        b = room(guild=2, state=replace(human(), self_deaf=True, self_stream=True))
        result = VoiceXpPolicy().explain(timeline(a, b), 1)
        self.assertEqual(result.total, 1200)
        self.assertEqual(result.stream_bonus, 0)
        self.assertEqual(result.audio_reduction, 0)

    def test_equal_rates_choose_stable_context_and_its_whole_breakdown(self) -> None:
        a = room(guild=1, state=replace(human(), self_video=True))
        b = room(guild=2, state=replace(human(), self_mute=True, self_stream=True))
        policy = VoiceXpPolicy()
        first = policy.explain(timeline(a, b), 1)
        self.assertEqual(first, policy.explain(timeline(b, a), 1))
        self.assertEqual(first.total, 1320)
        self.assertEqual(first.video_bonus, 120)
        self.assertEqual(first.stream_bonus, 0)

    def test_gap_or_afk_in_one_context_does_not_cancel_another(self) -> None:
        gap = ObservationGap(at(0), at(3600), GapReason.UNKNOWN, 1)
        for a in (
            room(guild=1, gaps=(gap,)),
            room(guild=1, state=replace(human(), afk=True)),
        ):
            with self.subTest(a=a):
                self.assertEqual(
                    VoiceXpPolicy().calculate(timeline(a, room(guild=2)), 1), 1200
                )

    def test_filter_and_clip_before_selecting_winner(self) -> None:
        a = room(start=0, end=3600, guild=1)
        b = room(
            start=1800, end=5400, guild=2, state=replace(human(), self_stream=True)
        )
        sample = timeline(a, b)
        policy = VoiceXpPolicy()
        self.assertEqual(
            policy.calculate(
                sample, 1, VoiceScope(time_range=TimeRange(at(900), at(2700)))
            ),
            Fraction(675),
        )
        self.assertEqual(
            policy.calculate(
                sample, 1, VoiceScope(1, 10, TimeRange(at(900), at(2700)))
            ),
            600,
        )
        self.assertEqual(policy.calculate(sample, 1, VoiceScope(1, 99)), 0)

    def test_touching_intervals_do_not_overlap(self) -> None:
        sample = timeline(
            room(end=1800, guild=1),
            room(start=1800, guild=2, state=replace(human(), self_stream=True)),
        )
        self.assertEqual(VoiceXpPolicy().calculate(sample, 1), 1350)

    def test_segmentation_and_replay_order_do_not_change_xp(self) -> None:
        state = replace(human(), self_mute=True, self_stream=True, self_video=True)
        whole = room(state=state)
        fragments = tuple(
            replace(whole, started_at=at(i), ended_at=at(i + 60))
            for i in range(0, 3600, 60)
        )
        policy = VoiceXpPolicy()
        expected = policy.explain(timeline(whole), 1)
        self.assertEqual(policy.explain(timeline(*fragments), 1), expected)
        self.assertEqual(policy.explain(timeline(*reversed(fragments)), 1), expected)
        self.assertEqual(policy.explain(timeline(whole, whole), 1), expected)

    def test_fractional_microseconds_never_round_per_piece(self) -> None:
        first = replace(room(), ended_at=at(0) + timedelta(microseconds=1))
        second = replace(
            room(),
            started_at=first.ended_at,
            ended_at=at(0) + timedelta(microseconds=2),
        )
        policy = VoiceXpPolicy()
        self.assertEqual(
            policy.calculate(timeline(first, second), 1), Fraction(1, 1_500_000)
        )

    def test_query_partition_preserves_total(self) -> None:
        sample = timeline(
            room(guild=1),
            room(
                guild=2, start=1500, end=5000, state=replace(human(), self_stream=True)
            ),
        )
        policy = VoiceXpPolicy()
        first = policy.calculate(
            sample, 1, VoiceScope(time_range=TimeRange(at(0), at(2222)))
        )
        second = policy.calculate(
            sample, 1, VoiceScope(time_range=TimeRange(at(2222), at(5000)))
        )
        self.assertEqual(first + second, policy.calculate(sample, 1))

    def test_utc_duration_does_not_depend_on_datetime_display_zone(self) -> None:
        sample = room()
        zone = timezone(timedelta(hours=5, minutes=45))
        localized = replace(
            sample,
            started_at=sample.started_at.astimezone(zone),
            ended_at=sample.ended_at.astimezone(zone),
        )
        self.assertEqual(VoiceXpPolicy().calculate(timeline(localized), 1), 1200)

    def test_sweep_matches_small_independent_per_second_oracle(self) -> None:
        policy = VoiceXpPolicy()
        states = (
            human(),
            replace(human(), self_stream=True),
            replace(human(), self_deaf=True),
        )
        for choices in product(states, repeat=3):
            candidates = tuple(
                room(start=i * 3, end=10 + i * 2, guild=i + 1, state=state)
                for i, state in enumerate(choices)
            )
            expected = Fraction()
            for second in range(14):
                rates = [
                    policy.rate(r, 1).total
                    for r in candidates
                    if r.started_at <= at(second) < r.ended_at
                ]
                expected += max(rates, default=Fraction()) / 3600
            with self.subTest(choices=choices):
                self.assertEqual(policy.calculate(timeline(*candidates), 1), expected)
