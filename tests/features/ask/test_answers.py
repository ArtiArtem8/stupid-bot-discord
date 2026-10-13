"""Verify repeatable answer selection and question normalization."""

from __future__ import annotations

import unittest

from stupidbot.features.ask.answers import random_answer, str_local


class TestAnswers(unittest.TestCase):
    def test_random_answer_deterministic_and_in_answers(self) -> None:
        answers = ["A", "B", "C", "D"]
        out1 = random_answer("Hello", answers)
        out2 = random_answer("Hello", answers)
        self.assertEqual(out1, out2)
        self.assertIn(out1, answers)

    def test_random_answer_branches_mod7_mod3_mod5_else_v1(self) -> None:
        answers = ["A", "B", "C", "D", "E", "F"]

        out_mod7 = random_answer("b", answers)
        self.assertIn(out_mod7, answers)
        out_mod3 = random_answer("c", answers)
        self.assertIn(out_mod3, answers)
        out_mod5 = random_answer("d", answers)
        self.assertIn(out_mod5, answers)
        out_v1_else = random_answer("da", answers)
        self.assertIn(out_v1_else, answers)
        out_v17 = random_answer("a" * 18, answers)
        self.assertIn(out_v17, answers)

    def test_str_local_filters_and_lowercases(self) -> None:
        self.assertEqual(str_local("ПрИвЕт! Hello_123"), "приветhello123")

    def test_str_local_cache_hits(self) -> None:
        str_local.cache_clear()
        self.addCleanup(str_local.cache_clear)
        info0 = str_local.cache_info()
        self.assertEqual(info0.hits, 0)

        _ = str_local("abc")
        info1 = str_local.cache_info()
        self.assertEqual(info1.misses, 1)

        _ = str_local("abc")
        info2 = str_local.cache_info()
        self.assertGreaterEqual(info2.hits, 1)
