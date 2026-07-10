"""Tests for music queue management."""

from __future__ import annotations

import unittest

from api.music.queue import QueueManager
from tests.api.music.helpers import make_track


class TestQueueManager(unittest.TestCase):
    def test_append_adds_single_track(self) -> None:
        queue = QueueManager()
        track = make_track("one")

        queue.append(track)

        self.assertEqual(list(queue), [track])

    def test_extend_adds_multiple_tracks(self) -> None:
        queue = QueueManager()
        tracks = [make_track("one"), make_track("two")]

        queue.extend(tracks)

        self.assertEqual(list(queue), tracks)

    def test_prepend_adds_single_track_to_front(self) -> None:
        queue = QueueManager()
        first = make_track("first")
        second = make_track("second")

        queue.append(second)
        queue.prepend(first)

        self.assertEqual(list(queue), [first, second])

    def test_extend_front_preserves_input_order(self) -> None:
        queue = QueueManager()
        existing = make_track("existing")
        tracks = [make_track("one"), make_track("two"), make_track("three")]

        queue.append(existing)
        queue.extend_front(tracks)

        self.assertEqual(list(queue), [*tracks, existing])

    def test_extend_front_accepts_empty_iterable(self) -> None:
        queue = QueueManager()
        track = make_track("existing")

        queue.append(track)
        queue.extend_front([])

        self.assertEqual(list(queue), [track])

    def test_pop_next_returns_and_removes_first_track(self) -> None:
        queue = QueueManager()
        tracks = [make_track("one"), make_track("two")]
        queue.extend(tracks)

        self.assertIs(queue.pop_next(), tracks[0])
        self.assertEqual(list(queue), [tracks[1]])

    def test_next_peeks_without_removing(self) -> None:
        queue = QueueManager()
        track = make_track("one")

        self.assertIsNone(queue.next)
        queue.append(track)

        self.assertIs(queue.next, track)
        self.assertEqual(list(queue), [track])

    def test_duration_sums_track_lengths(self) -> None:
        queue = QueueManager()
        queue.extend([make_track("one", length=1200), make_track("two", length=3400)])

        self.assertEqual(queue.duration, 4600)

    def test_clear_removes_all_tracks(self) -> None:
        queue = QueueManager()
        queue.extend([make_track("one"), make_track("two")])

        queue.clear()

        self.assertEqual(list(queue), [])
        self.assertTrue(queue.is_empty)

    def test_shuffle_preserves_track_composition(self) -> None:
        queue = QueueManager()
        tracks = [make_track("one"), make_track("two"), make_track("three")]
        queue.extend(tracks)

        queue.shuffle()

        self.assertCountEqual(list(queue), tracks)
