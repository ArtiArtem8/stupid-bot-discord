import unittest
from datetime import datetime

from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceJournalRecord,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.scope import TimeRange, VoiceScope
from tests.api.voice.examples import at, human


class TestVoiceModel(unittest.TestCase):
    def test_same_immutable_state_supports_bots_humans_and_unknowns(self) -> None:
        for bot in (False, True, None):
            with self.subTest(bot=bot):
                state = VoiceStateSnapshot(1, 10, is_bot=bot)
                self.assertIs(state.is_bot, bot)
                self.assertIsNone(state.self_mute)

    def test_rejects_ambiguous_or_invalid_boundaries(self) -> None:
        with self.assertRaises(ValueError):
            VoiceJournalRecord(1, "boot", at(0), 0, VoiceObservation(human()))
        with self.assertRaises(ValueError):
            VoiceSnapshot((human(), human()))
        with self.assertRaises(ValueError):
            VoiceSnapshot((VoiceStateSnapshot(1, None, channel_known=False),))
        with self.assertRaises(ValueError):
            ObservationGap(at(10), at(0), GapReason.UNKNOWN)
        with self.assertRaises(ValueError):
            TimeRange(datetime(2026, 1, 1), at(100))
        with self.assertRaises(ValueError):
            VoiceScope(channel_id=10)

    def test_raised_hand_timestamp_implies_true_but_true_needs_no_timestamp(
        self,
    ) -> None:
        state = VoiceStateSnapshot(1, 10, requested_to_speak_at=at(0))
        self.assertIs(state.requested_to_speak, True)
        legacy = VoiceStateSnapshot(1, 10, requested_to_speak=True)
        self.assertIsNone(legacy.requested_to_speak_at)
        with self.assertRaises(ValueError):
            VoiceStateSnapshot(
                1, 10, requested_to_speak=False, requested_to_speak_at=at(0)
            )
