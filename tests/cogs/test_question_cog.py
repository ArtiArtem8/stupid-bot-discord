"""Question history returns the committed winner and protects the RAM answer queue."""

import asyncio
import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from discord.app_commands import CommandInvokeError
from sqlalchemy import select

from cogs.question_cog import QuestionCog
from repositories.question_repository import QuestionRepository
from repositories.sqlite.schema import question_answers
from tests.storage import temporary_database
from utils.text_utils import str_local


class TestQuestionHistory(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repository = QuestionRepository(self.database)
        self.cog = QuestionCog(MagicMock(), self.repository)

    async def test_returns_existing_answer_without_rewriting_it(self) -> None:
        self.assertIsNone(await self.cog._add_to_history(1, "Question?", "answer"))
        self.assertEqual(
            await self.cog._add_to_history(1, "Question?", "other"), "answer"
        )
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(
                        question_answers.c.user_id,
                        question_answers.c.normalized_question,
                        question_answers.c.answer,
                    )
                )
            ).all()
        self.assertEqual(rows, [(1, str_local("Question?"), "answer")])

    async def test_concurrent_insert_returns_one_committed_winner(self) -> None:
        results = await asyncio.gather(
            *(self.repository.answer(1, "same", str(index)) for index in range(8))
        )
        self.assertEqual(sum(inserted for _, inserted in results), 1)
        self.assertEqual(len({answer for answer, _ in results}), 1)
        self.assertEqual(
            await self.repository.answer(2, "same", "different user"),
            ("different user", True),
        )

    async def test_failed_persistence_does_not_advance_queue_or_respond(self) -> None:
        self.cog.answers = ["first", "second"]
        interaction = MagicMock()
        interaction.user.id = 1
        interaction.response.send_message = AsyncMock()
        with patch.object(
            self.repository, "answer", side_effect=OSError("unavailable")
        ):
            with self.assertRaises(CommandInvokeError) as failure:
                await self.cog.q._do_call(interaction, {"text": "Question?"})
        self.assertIsInstance(failure.exception.original, OSError)
        self.assertEqual(self.cog.answers, ["first", "second"])
        interaction.response.send_message.assert_not_awaited()

    async def test_concurrent_questions_send_the_answer_they_commit(self) -> None:
        self.cog.answers = ["first", "second"]
        items = [MagicMock(), MagicMock()]
        for index, item in enumerate(items, start=1):
            item.user.id = index
            item.response.send_message = AsyncMock()
        await asyncio.gather(
            *(self.cog.q._do_call(item, {"text": "Question?"}) for item in items)
        )
        for index, item in enumerate(items, start=1):
            answer, inserted = await self.repository.answer(
                index, str_local("Question?"), "must not replace"
            )
            self.assertFalse(inserted)
            item.response.send_message.assert_awaited_once_with(answer)
