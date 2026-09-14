"""Tests for question-history persistence."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast, override
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.question_cog import QuestionCog
from utils.json_store import AsyncJsonFileStore
from utils.text_utils import str_local


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

    async def test_concurrent_new_questions_store_the_answers_they_send(self) -> None:
        self.cog.answers = ["answer-one", "answer-two"]
        first_waiting = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        original_add = self.cog._add_to_history

        async def coordinated_add(
            user_id: str, question: str, answer: str
        ) -> str | None:
            if user_id == "1":
                first_waiting.set()
                await release_first.wait()
            return await original_add(user_id, question, answer)

        def make_interaction(user_id: int) -> tuple[MagicMock, AsyncMock]:
            interaction = MagicMock()
            interaction.user.id = user_id
            send = AsyncMock()
            interaction.response.send_message = send
            return interaction, send

        async def invoke(
            interaction: MagicMock, text: str, started: asyncio.Event | None = None
        ) -> None:
            if started is not None:
                started.set()
            await cast(Any, QuestionCog.q).callback(self.cog, interaction, text=text)

        first_interaction, first_send = make_interaction(1)
        second_interaction, second_send = make_interaction(2)

        with patch.object(self.cog, "_add_to_history", side_effect=coordinated_add):
            first = asyncio.create_task(invoke(first_interaction, "First?"))
            await asyncio.wait_for(first_waiting.wait(), timeout=2)
            second = asyncio.create_task(
                invoke(second_interaction, "Second?", second_started)
            )
            await asyncio.wait_for(second_started.wait(), timeout=2)
            release_first.set()
            await asyncio.wait_for(asyncio.gather(first, second), timeout=2)

        first_call = first_send.await_args
        second_call = second_send.await_args
        if first_call is None or second_call is None:
            self.fail("expected both question responses to be sent")

        data = await self.store.read()
        first_history = cast(dict[str, object], data["1"])
        second_history = cast(dict[str, object], data["2"])
        self.assertEqual(first_history[str_local("First?")], first_call.args[0])
        self.assertEqual(second_history[str_local("Second?")], second_call.args[0])
