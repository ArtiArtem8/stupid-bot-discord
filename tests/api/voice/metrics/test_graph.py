import unittest

from api.voice.metrics.graph import shared_seconds
from tests.api.voice.examples import example


class TestGraph(unittest.TestCase):
    def test_graph_is_a_projection_of_the_same_timeline(self) -> None:
        self.assertEqual(
            shared_seconds(example()), {(1, 2): 600, (1, 3): 600, (2, 3): 300}
        )
