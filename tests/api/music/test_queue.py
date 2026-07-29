"""Tests for music queue management."""

from __future__ import annotations

import unittest

from api.music.queue import QueueManager
from tests.api.music.helpers import make_entry


class TestQueueManager(unittest.TestCase):
    def test_append_adds_single_track(self) -> None:
        queue = QueueManager()
        track = make_entry("one")

        queue.append(track)

        self.assertEqual(list(queue), [track])

    def test_extend_adds_multiple_tracks(self) -> None:
        queue = QueueManager()
        tracks = [make_entry("one"), make_entry("two", entry_id=2)]

        queue.extend(tracks)

        self.assertEqual(list(queue), tracks)

    def test_prepend_adds_single_track_to_front(self) -> None:
        queue = QueueManager()
        first = make_entry("first")
        second = make_entry("second", entry_id=2)

        queue.append(second)
        queue.prepend(first)

        self.assertEqual(list(queue), [first, second])

    def test_extend_front_preserves_input_order(self) -> None:
        queue = QueueManager()
        existing = make_entry("existing")
        tracks = [
            make_entry("one", entry_id=2),
            make_entry("two", entry_id=3),
            make_entry("three", entry_id=4),
        ]

        queue.append(existing)
        queue.extend_front(tracks)

        self.assertEqual(list(queue), [*tracks, existing])

    def test_extend_front_accepts_empty_iterable(self) -> None:
        queue = QueueManager()
        track = make_entry("existing")

        queue.append(track)
        queue.extend_front([])

        self.assertEqual(list(queue), [track])

    def test_pop_next_returns_and_removes_first_track(self) -> None:
        queue = QueueManager()
        tracks = [make_entry("one"), make_entry("two", entry_id=2)]
        queue.extend(tracks)

        self.assertIs(queue.pop_next(), tracks[0])
        self.assertEqual(list(queue), [tracks[1]])

    def test_remove_entries_returns_matches_in_queue_order(self) -> None:
        first = make_entry("first", entry_id=1)
        middle = make_entry("middle", entry_id=2)
        last = make_entry("last", entry_id=3)
        queue = QueueManager()
        queue.extend((first, middle, last))

        removed = queue.remove_entries((last, first))

        self.assertEqual(removed, (first, last))
        self.assertEqual(list(queue), [middle])

    def test_remove_entries_rejects_lookalike_objects(self) -> None:
        queued = make_entry("same", entry_id=1)
        lookalike = make_entry("same", entry_id=1)
        queue = QueueManager()
        queue.append(queued)

        self.assertEqual(queue.remove_entries((lookalike,)), ())
        self.assertEqual(list(queue), [queued])

    def test_remove_entries_with_empty_expected_preserves_queue(self) -> None:
        entries = [make_entry("one"), make_entry("two", entry_id=2)]
        queue = QueueManager()
        queue.extend(entries)

        self.assertEqual(queue.remove_entries(()), ())
        self.assertEqual(list(queue), entries)

    def test_remove_entries_preserves_interleaved_entries_relative_order(self) -> None:
        requested = (
            make_entry("requested-one", entry_id=1),
            make_entry("requested-two", entry_id=3),
        )
        others = (
            make_entry("other-one", entry_id=2),
            make_entry("other-two", entry_id=4),
        )
        queue = QueueManager()
        queue.extend((requested[0], others[0], requested[1], others[1]))

        removed = queue.remove_entries(requested)

        self.assertEqual(removed, requested)
        self.assertEqual(list(queue), list(others))

    def test_next_peeks_without_removing(self) -> None:
        queue = QueueManager()
        track = make_entry("one")

        self.assertIsNone(queue.next)
        queue.append(track)

        self.assertIs(queue.next, track)
        self.assertEqual(list(queue), [track])

    def test_duration_sums_track_lengths(self) -> None:
        queue = QueueManager()
        queue.extend(
            [
                make_entry("one", length=1200),
                make_entry("two", entry_id=2, length=3400),
            ]
        )

        self.assertEqual(queue.duration, 4600)

    def test_clear_removes_all_tracks(self) -> None:
        queue = QueueManager()
        queue.extend([make_entry("one"), make_entry("two", entry_id=2)])

        queue.clear()

        self.assertEqual(list(queue), [])
        self.assertTrue(queue.is_empty)

    def test_shuffle_preserves_track_composition(self) -> None:
        queue = QueueManager()
        tracks = [
            make_entry("one"),
            make_entry("two", entry_id=2),
            make_entry("three", entry_id=3),
        ]
        queue.extend(tracks)

        queue.shuffle()

        self.assertCountEqual(list(queue), tracks)
