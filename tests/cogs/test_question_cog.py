"""Tests for question-history persistence."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import MagicMock

from cogs.question_cog import QuestionCog
from utils import AsyncJsonFileStore, str_local


class TestQuestionHistory(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = AsyncJsonFileStore(
            Path(self.temp_dir.name) / "answers.json", backup_amount=0
        )
        self.cog = QuestionCog(MagicMock())
        self.cog._history_store = self.store

    async def test_returns_existing_answer_without_rewriting_it(self) -> None:
        first = await self.cog._add_to_history("1", "Question?", "answer")
        second = await self.cog._add_to_history("1", "Question?", "other")

        self.assertIsNone(first)
        self.assertEqual(second, "answer")
        data = await self.store.read()
        self.assertEqual(data["1"], {str_local("Question?"): "answer"})

    async def test_different_questions_are_both_preserved(self) -> None:
        await self.cog._add_to_history("1", "First?", "yes")
        await self.cog._add_to_history("1", "Second?", "no")

        data = await self.store.read()
        self.assertEqual(
            data["1"],
            {
                str_local("First?"): "yes",
                str_local("Second?"): "no",
            },
        )

    async def test_invalid_user_record_is_preserved(self) -> None:
        await self.store.write({"1": "invalid"})

        with self.assertRaisesRegex(ValueError, "invalid user record"):
            await self.cog._add_to_history("1", "Question?", "answer")

        self.assertEqual(await self.store.read(), {"1": "invalid"})
