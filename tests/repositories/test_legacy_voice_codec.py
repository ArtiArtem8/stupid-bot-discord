import json
import unittest

from api.voice.metrics.presence import presence
from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.timeline import build_timeline
from tests.api.voice.examples import START, at, human, record
from tests.repositories.legacy_voice import encode_record
from tools.storage_legacy.voice_codec import decode_record


class TestVoiceCodec(unittest.TestCase):
    def test_v2_round_trip_preserves_full_state_and_unknown_fields(self) -> None:
        state = VoiceStateSnapshot(
            1,
            10,
            False,
            self_mute=True,
            self_deaf=False,
            server_mute=True,
            server_deaf=False,
            self_stream=True,
            self_video=True,
            suppress=False,
            requested_to_speak_at=at(2),
            session_id="session",
            afk=False,
        )
        for fact in (
            VoiceObservation(state),
            VoiceSnapshot((state,)),
            VoiceSnapshot((state,), authoritative=False),
            ObservationGap(at(0), at(10), GapReason.WRITE_FAILURE, 1),
            VoiceCheckpoint(),
        ):
            with self.subTest(fact=fact):
                item = record(10, fact)
                self.assertEqual(decode_record(encode_record(item)), item)
        encoded = json.loads(encode_record(record(10, VoiceObservation(state))))
        self.assertEqual(encoded["schema_version"], 2)
        self.assertIn("self_stream", encoded["state"])
        self.assertNotIn("sm", encoded["state"])

    def test_legacy_snapshot_does_not_invent_missing_flags(self) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "startup",
                    "guild": 1,
                    "detail": {"presence": {"10": [1]}, "bot_presence": {"10": [9]}},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceSnapshot)
        if isinstance(item.fact, VoiceSnapshot):
            human_state, bot = item.fact.states
            self.assertIsNone(human_state.self_mute)
            self.assertIsNone(human_state.session_id)
            self.assertIsNone(human_state.requested_to_speak_at)
            self.assertIs(human_state.is_bot, False)
            self.assertIs(bot.is_bot, True)

    def test_legacy_flags_are_read_but_raised_hand_cannot_invent_timestamp(
        self,
    ) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "flags",
                    "guild": 1,
                    "user": 1,
                    "channel_after": 10,
                    "flags_after": {"sm": True, "sd": False, "st": True, "hr": True},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceObservation)
        if isinstance(item.fact, VoiceObservation):
            self.assertIs(item.fact.state.self_mute, True)
            self.assertIs(item.fact.state.self_deaf, False)
            self.assertIs(item.fact.state.self_stream, True)
            self.assertIsNone(item.fact.state.self_video)
            self.assertIsNone(item.fact.state.requested_to_speak_at)

    def test_legacy_heartbeat_without_maps_cannot_close_gap(self) -> None:
        legacy = decode_record(
            json.dumps(
                {
                    "seq": 300,
                    "boot": "one",
                    "at": at(30).isoformat(),
                    "mono": 30,
                    "kind": "heartbeat",
                    "guild": 1,
                    "detail": {"members": 1},
                }
            )
        )
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(
                    10,
                    ObservationGap(at(10), None, GapReason.GATEWAY_DISCONNECT),
                    guild=None,
                ),
                legacy,
                record(40, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)

    def test_unknown_version_and_corrupt_schema_fail_explicitly(self) -> None:
        for line in ('{"schema_version": 3}', "not json", '{"schema_version": 2}'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                decode_record(line)

    def test_partial_legacy_snapshot_is_not_claimed_as_a_full_population(self) -> None:
        item = decode_record(
            json.dumps(
                {
                    "seq": 1,
                    "boot": "old",
                    "at": START.isoformat(),
                    "mono": 0,
                    "kind": "startup",
                    "guild": 1,
                    "detail": {"presence": {"10": [1]}},
                }
            )
        )
        self.assertIsInstance(item.fact, VoiceSnapshot)
        if isinstance(item.fact, VoiceSnapshot):
            self.assertFalse(item.fact.authoritative)
            self.assertEqual(item.fact.states[0].user_id, 1)

    def test_legacy_overflow_after_a_snapshot_does_not_confirm_the_lost_window(
        self,
    ) -> None:
        overflow = decode_record(
            json.dumps(
                {
                    "seq": 301,
                    "boot": "one",
                    "at": at(30).isoformat(),
                    "mono": 30,
                    "kind": "overflow",
                    "detail": {"dropped": 2},
                }
            )
        )
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(),))),
                record(20, VoiceCheckpoint()),
                record(30, VoiceSnapshot((human(),))),
                overflow,
                record(40, VoiceSnapshot((human(),))),
                record(50, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(presence(timeline, 1).total_seconds, 10)

    def test_legacy_raised_hand_preserves_true_false_and_unknown(self) -> None:
        for flags, expected in (
            ({"hr": True}, True),
            ({"hr": False}, False),
            ({}, None),
        ):
            with self.subTest(flags=flags):
                item = decode_record(
                    json.dumps(
                        {
                            "seq": 1,
                            "boot": "old",
                            "at": START.isoformat(),
                            "mono": 0,
                            "kind": "flags",
                            "guild": 1,
                            "user": 1,
                            "channel_after": 10,
                            "flags_after": flags,
                        }
                    )
                )
                self.assertIsInstance(item.fact, VoiceObservation)
                if isinstance(item.fact, VoiceObservation):
                    self.assertIs(item.fact.state.requested_to_speak, expected)
                    self.assertIsNone(item.fact.state.requested_to_speak_at)
                    self.assertEqual(decode_record(encode_record(item)), item)

    def test_earlier_v2_without_raised_hand_boolean_keeps_unknown_null(self) -> None:
        raw = {
            "schema_version": 2,
            "sequence": 1,
            "boot_id": "v2",
            "observed_at": START.isoformat(),
            "monotonic": 0,
            "guild_id": 1,
            "kind": "observation",
            "state": {"user_id": 1, "channel_id": 10, "requested_to_speak_at": None},
        }
        item = decode_record(json.dumps(raw))
        self.assertIsInstance(item.fact, VoiceObservation)
        if isinstance(item.fact, VoiceObservation):
            self.assertIsNone(item.fact.state.requested_to_speak)
