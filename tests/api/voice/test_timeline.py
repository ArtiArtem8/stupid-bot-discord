import unittest
from dataclasses import replace

from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.scope import VoiceScope, observed_rooms
from api.voice.timeline import ObservationInterval, build_timeline
from tests.api.voice.examples import at, human, record


class TestVoiceTimeline(unittest.TestCase):
    def test_startup_join_move_flags_and_leave_preserve_states(self) -> None:
        initial = human()
        flags = replace(
            initial,
            channel_id=20,
            self_mute=True,
            self_deaf=True,
            self_stream=True,
            self_video=True,
            server_mute=False,
            server_deaf=False,
            suppress=True,
            requested_to_speak_at=at(12),
        )
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((initial,))),
                record(5, VoiceObservation(human(2))),
                record(10, VoiceObservation(replace(initial, channel_id=20))),
                record(15, VoiceObservation(flags)),
                record(20, VoiceObservation(human(1, None))),
                record(30, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 20)
        self.assertEqual(presence(timeline, 1).session_count, 1)
        self.assertEqual(presence(timeline, 2).total_seconds, 25)
        self.assertEqual(presence(timeline, 1, VoiceScope(1, 10)).total_seconds, 10)
        self.assertIn(flags, timeline.rooms[-2].states)
        for room in timeline.rooms:
            self.assertGreater(room.ended_at, room.started_at)

    def test_gap_is_not_closed_by_checkpoint_observation_or_local_snapshot(
        self,
    ) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(
                    10,
                    ObservationGap(at(10), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                record(20, VoiceCheckpoint()),
                record(30, VoiceSnapshot((human(),), authoritative=False)),
                record(40, VoiceObservation(human())),
                record(50, VoiceSnapshot((human(),))),
                record(60, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 20)
        self.assertEqual(presence(timeline, 1).session_count, 2)
        self.assertEqual(
            [(gap.started_at, gap.ended_at) for gap in timeline.gaps],
            [(at(10), at(50))],
        )

    def test_snapshot_confirms_only_its_guild_after_global_disconnect(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),)), guild=1),
                record(0, VoiceSnapshot((human(2),)), guild=2, sequence=1),
                record(
                    10,
                    ObservationGap(at(10), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                record(20, VoiceSnapshot((human(),)), guild=1),
                record(40, VoiceCheckpoint(), guild=None),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 30)
        self.assertEqual(presence(timeline, 2).total_seconds, 10)
        self.assertIsNone(timeline.gaps[-1].ended_at)

    def test_restart_never_bridges_boot_ids(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(10, VoiceCheckpoint()),
                record(100, VoiceCheckpoint(), boot="two"),
                record(110, VoiceSnapshot((human(),)), boot="two"),
                record(120, VoiceCheckpoint(), boot="two"),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 20)
        self.assertEqual(timeline.gaps[0].reason, GapReason.PROCESS_RESTART)
        self.assertEqual(timeline.gaps[0].started_at, at(10))

    def test_retrospective_loss_invalidates_already_emitted_intervals(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(20, VoiceCheckpoint()),
                record(
                    40,
                    ObservationGap(at(10), None, GapReason.WRITE_FAILURE),
                    guild=None,
                ),
                record(50, VoiceSnapshot((human(),))),
                record(60, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 20)

    def test_unanchored_observations_and_open_tail_are_not_credited(self) -> None:
        timeline = build_timeline(
            [record(0, VoiceObservation(human())), record(60, VoiceCheckpoint())]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 0)
        self.assertIsNone(timeline.gaps[0].ended_at)
        self.assertEqual(timeline.rooms[-1].ended_at, at(60))

    def test_clock_rollback_uses_sequence_and_does_not_duplicate_time(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(20, VoiceCheckpoint()),
                replace(
                    record(10, VoiceSnapshot((human(),)), sequence=300), monotonic=30
                ),
                replace(
                    record(30, VoiceSnapshot((human(),)), sequence=400), monotonic=50
                ),
                replace(record(40, VoiceCheckpoint(), sequence=500), monotonic=60),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 20)
        self.assertTrue(
            any(g.reason == GapReason.CLOCK_DISCONTINUITY for g in timeline.gaps)
        )

    def test_stopped_guild_does_not_continue_into_other_guild_history(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(10, VoiceLifecycle(stopped=True)),
                record(30, VoiceCheckpoint(), guild=None),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)

    def test_unknown_user_and_channel_are_not_silently_human_or_leave(self) -> None:
        state = VoiceStateSnapshot(9, 10)
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(), state))),
                record(10, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 9).total_seconds, 0)
        self.assertEqual(presence(timeline, 1).solo_seconds, 0)
        self.assertIn(state, timeline.rooms[0].states)

    def test_snapshot_drift_does_not_invent_the_time_of_a_missed_leave(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(10, VoiceCheckpoint()),
                record(20, VoiceSnapshot(())),
                record(30, VoiceObservation(human())),
                record(40, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)
        self.assertEqual(
            (timeline.gaps[0].started_at, timeline.gaps[0].ended_at), (at(0), at(20))
        )

    def test_streaming_time_needs_only_existing_timeline_state(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((replace(human(), self_stream=False),))),
                record(10, VoiceObservation(replace(human(), self_stream=True))),
                record(30, VoiceObservation(replace(human(), self_stream=False))),
                record(40, VoiceCheckpoint()),
            ]
        )
        streaming_seconds = sum(
            room.seconds
            for room in observed_rooms(timeline)
            if any(
                state.user_id == 1 and state.self_stream is True
                for state in room.states
            )
        )
        self.assertEqual(streaming_seconds, 20)
        self.assertEqual(presence(timeline, 1).session_count, 1)

    def test_empty_guild_has_full_observed_coverage(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot(())),
                record(86400, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(timeline.rooms, ())
        self.assertEqual(timeline.gaps, ())
        self.assertEqual(timeline.coverage, (ObservationInterval(1, at(0), at(86400)),))
        self.assertEqual(build_timeline([]).coverage, ())

    def test_disconnect_splits_empty_coverage_until_authoritative_snapshot(
        self,
    ) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot(())),
                record(
                    10,
                    ObservationGap(at(10), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                record(20, VoiceCheckpoint()),
                record(30, VoiceSnapshot((), authoritative=False)),
                record(40, VoiceSnapshot(())),
                record(60, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(
            timeline.coverage,
            (
                ObservationInterval(1, at(0), at(10)),
                ObservationInterval(1, at(40), at(60)),
            ),
        )

    def test_local_observations_cannot_establish_initial_coverage(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((), authoritative=False)),
                record(10, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(timeline.coverage, ())

    def test_retrospective_overlapping_gaps_remove_coverage_once(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot(())),
                record(10, VoiceCheckpoint()),
                record(
                    30, ObservationGap(at(5), None, GapReason.WRITE_FAILURE), guild=None
                ),
                record(
                    40,
                    ObservationGap(at(20), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                record(50, VoiceSnapshot(())),
                record(60, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(
            timeline.coverage,
            (
                ObservationInterval(1, at(0), at(5)),
                ObservationInterval(1, at(50), at(60)),
            ),
        )

    def test_restart_does_not_bridge_empty_guild_coverage(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot(())),
                record(10, VoiceCheckpoint()),
                record(100, VoiceSnapshot(()), boot="two"),
                record(120, VoiceCheckpoint(), boot="two"),
            ]
        )
        self.assertEqual(
            timeline.coverage,
            (
                ObservationInterval(1, at(0), at(10)),
                ObservationInterval(1, at(100), at(120)),
            ),
        )
