import unittest

from api.voice.metrics.xp import VoiceXpPolicyV1
from tests.api.voice.examples import example


class TestXp(unittest.TestCase):
    def test_policy_is_explicit_and_does_not_mutate_the_timeline(self) -> None:
        timeline = example()
        self.assertEqual(VoiceXpPolicyV1().calculate(timeline, 1), 20)
        self.assertEqual(VoiceXpPolicyV1(2).calculate(timeline, 1), 40)
        self.assertEqual(VoiceXpPolicyV1().calculate(timeline, 1), 20)
        with self.assertRaises(ValueError):
            VoiceXpPolicyV1(-1)
