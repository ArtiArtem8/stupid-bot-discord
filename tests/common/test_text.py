from __future__ import annotations

import unittest

from stupidbot.common.text import (
    TextPaginator,
    truncate_sequence,
    truncate_text,
)


class TestTextUtils(unittest.TestCase):
    def test_truncate_text_no_truncation(self) -> None:
        self.assertEqual(truncate_text("abc", 3), "abc")
        self.assertEqual(truncate_text("abc", 10), "abc")

    def test_truncate_text_placeholder_longer_than_width(self) -> None:
        self.assertEqual(truncate_text("abcdef", 2, placeholder="..."), "..")
        self.assertEqual(truncate_text("abcdef", 3, placeholder="..."), "...")

    def test_truncate_text_mode_end(self) -> None:
        out = truncate_text("abcdef", 5, mode="end")
        self.assertEqual(len(out), 5)
        self.assertTrue(out.endswith("..."))

    def test_truncate_text_mode_start(self) -> None:
        out = truncate_text("abcdef", 5, mode="start")
        self.assertEqual(len(out), 5)
        self.assertTrue(out.startswith("..."))

    def test_truncate_text_start_with_no_room_for_content(self) -> None:
        self.assertEqual(truncate_text("abcdef", 3, mode="start"), "...")
        self.assertEqual(truncate_text("abcdef", 0, placeholder="", mode="start"), "")

    def test_truncate_text_mode_middle_right_len_zero(self) -> None:
        out = truncate_text("abcdef", 4, mode="middle")
        self.assertEqual(out, "a...")

    def test_truncate_text_mode_middle_balanced(self) -> None:
        out = truncate_text("abcdefghij", 7, mode="middle")
        self.assertEqual(len(out), 7)
        self.assertIn("...", out)

    def test_truncate_sequence_max_length_le_zero(self) -> None:
        self.assertEqual(truncate_sequence(["a", "b"], 0), "")
        self.assertEqual(truncate_sequence(["a", "b"], -1), "")

    def test_truncate_sequence_empty_items(self) -> None:
        self.assertEqual(truncate_sequence([], 10), "")

    def test_truncate_sequence_no_truncation(self) -> None:
        self.assertEqual(
            truncate_sequence(["aaa", "bbb"], 100, separator="|"), "aaa|bbb"
        )

    def test_truncate_sequence_max_length_le_placeholder(self) -> None:
        self.assertEqual(
            truncate_sequence(["aaaa", "bbbb"], 2, placeholder="..."), ".."
        )

    def test_truncate_sequence_a(self) -> None:
        self.assertEqual(
            truncate_sequence(["aaaa", "aaaa"], 7, placeholder="bbb"), "aaaabbb"
        )

    def test_truncate_sequence_truncate_at_separator_boundary(self) -> None:
        out = truncate_sequence(
            ["aaa", "bbb", "ccc"], 8, separator="|", placeholder="..."
        )
        self.assertLessEqual(len(out), 8)
        self.assertEqual(out, "aaa...")

    def test_truncate_sequence_truncate_at_separator_boundary_no_placeholder(
        self,
    ) -> None:
        out = truncate_sequence(["aaa", "bbb", "ccc"], 8, separator="|", placeholder="")
        self.assertLessEqual(len(out), 8)
        self.assertEqual(out, "aaa|bbb")

    def test_truncate_sequence_no_separator_found_falls_back_to_truncate_text(
        self,
    ) -> None:
        out = truncate_sequence(["abcdef"], 5, separator="|", placeholder="@")
        self.assertEqual(out, truncate_text("abcdef", 5, placeholder="@", mode="end"))

    def test_truncate_sequence_long_separator_boundary(self) -> None:
        sep = "<<<SEP_IS_VERY_LONG>>>"
        items = ["aa", "bb", "cc"]

        out = truncate_sequence(items, max_length=20, separator=sep, placeholder="...")
        self.assertLessEqual(len(out), 20)
        self.assertEqual(out, "aa...")
        self.assertTrue(out.endswith("..."))

    def test_truncate_sequence_no_partial_separator(self) -> None:
        sep = "___"
        out = truncate_sequence(
            ["aa", "bb"], max_length=6, separator=sep, placeholder="..."
        )
        self.assertEqual(out, "aa...")

    def test_truncate_sequence_first_item_too_long(self) -> None:
        out = truncate_sequence(["AAAAA", "B"], max_length=4, placeholder=".")
        self.assertEqual(out, "AAA.")

    def test_text_paginator_total_items_and_pages_empty(self) -> None:
        p = TextPaginator([], page_size=2, max_length=10, separator="\n")
        self.assertEqual(p.total_items, 0)
        self.assertEqual(p.pages, [])

    def test_text_paginator_truncates_long_line(self) -> None:
        p = TextPaginator(["X" * 10], page_size=10, max_length=5, separator="\n")
        self.assertEqual(p.total_items, 1)
        self.assertEqual(p.pages, [truncate_text("X" * 10, 5)])

    def test_text_paginator_splits_by_page_size(self) -> None:
        p = TextPaginator(["a", "b"], page_size=1, max_length=100, separator="\n")
        self.assertEqual(p.pages, ["a", "b"])

    def test_text_paginator_splits_by_max_length(self) -> None:
        p = TextPaginator(["aa", "bb"], page_size=10, max_length=3, separator="\n")
        self.assertEqual(p.pages, ["aa", "bb"])

    def test_text_paginator_long_separator_forces_new_page(self) -> None:
        sep = "<<<SEP_IS_VERY_LONG>>>"
        p = TextPaginator(
            ["a", "b"], page_size=10, max_length=len(sep) + 1, separator=sep
        )

        self.assertEqual(p.pages, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
