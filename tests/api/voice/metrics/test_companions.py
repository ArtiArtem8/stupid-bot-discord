import unittest

from api.voice.metrics.companions import companions
from api.voice.model import VoiceCheckpoint, VoiceSnapshot, VoiceStateSnapshot
from api.voice.scope import VoiceScope
from api.voice.timeline import build_timeline
from tests.api.voice.examples import example, human, record


class TestCompanions(unittest.TestCase):
    def test_pair_overlap_is_not_doubled(self) -> None:
        timeline = example()
        a = {stat.user_id: stat for stat in companions(timeline, 1)}
        b = {stat.user_id: stat for stat in companions(timeline, 2)}
        self.assertEqual(a[2].shared_seconds, 600)
        self.assertEqual(a[3].shared_seconds, 600)
        self.assertEqual(b[3].shared_seconds, 300)
        self.assertEqual(a[2].private_seconds, 300)
        self.assertEqual(a[3].private_seconds, 300)

    def test_different_channels_do_not_overlap(self) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(), human(2, 20)))),
                record(600, VoiceCheckpoint()),
            ]
        )
        self.assertEqual(companions(timeline, 1), ())

    def test_two_humans_with_music_bot_are_private(self) -> None:
        timeline = build_timeline(
            [
                record(
                    0,
                    VoiceSnapshot((human(), human(2), VoiceStateSnapshot(9, 10, True))),
                ),
                record(600, VoiceCheckpoint()),
            ]
        )
        stat = companions(timeline, 1)[0]
        self.assertEqual(
            (stat.user_id, stat.shared_seconds, stat.private_seconds), (2, 600, 600)
        )

    def test_unidentified_participant_prevents_private_credit(self) -> None:
        timeline = build_timeline(
            [
                record(
                    0, VoiceSnapshot((human(), human(2), VoiceStateSnapshot(9, 10)))
                ),
                record(600, VoiceCheckpoint()),
            ]
        )
        stat = companions(timeline, 1)[0]
        self.assertEqual((stat.shared_seconds, stat.private_seconds), (600, 0))

    def test_global_co_presence_adds_guild_scopes_without_cross_guild_pairs(
        self,
    ) -> None:
        timeline = build_timeline(
            [
                record(0, VoiceSnapshot((human(), human(2)))),
                record(
                    0, VoiceSnapshot((human(1, 20), human(3, 20))), guild=2, sequence=1
                ),
                record(60, VoiceCheckpoint(), guild=None),
            ]
        )
        global_stats = companions(timeline, 1)
        self.assertEqual(
            [(stat.user_id, stat.shared_seconds) for stat in global_stats],
            [(2, 60), (3, 60)],
        )
        self.assertEqual(
            global_stats,
            (
                *companions(timeline, 1, VoiceScope(1)),
                *companions(timeline, 1, VoiceScope(2)),
            ),
        )
        self.assertEqual([stat.user_id for stat in companions(timeline, 2)], [1])
