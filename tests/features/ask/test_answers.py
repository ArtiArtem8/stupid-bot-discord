"""Verify repeatable answer selection and question normalization."""

from __future__ import annotations

import unittest

from stupidbot.features.ask.answers import random_answer, str_local


class TestAnswers(unittest.TestCase):
    def test_answer_selection_depends_on_the_question_and_is_repeatable(self) -> None:
        answers = ["A", "B", "C", "D", "E", "F"]
        first = random_answer("b", answers)
        second = random_answer("c", answers)
        self.assertIn(first, answers)
        self.assertIn(second, answers)
        self.assertNotEqual(first, second)
        self.assertEqual(random_answer("b", answers), first)

    def test_str_local_filters_and_lowercases(self) -> None:
        self.assertEqual(str_local("ПрИвЕт! Hello_123"), "приветhello123")
