"""Question history returns the committed winner and protects the RAM answer queue."""

import asyncio
import unittest
from collections.abc import Coroutine
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from discord import Interaction
from discord.app_commands import CommandInvokeError
from sqlalchemy import select

from stupidbot.discord_ui.interaction_flow import run_with_defer
from stupidbot.features.ask import ask_cog
from stupidbot.features.ask.answers import str_local
from stupidbot.features.ask.ask_cog import QuestionCog
from stupidbot.features.ask.repository import QuestionRepository
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import Language, PresentationContext
from stupidbot.storage.schema import question_answers
from tests.features.preferences.fixtures import interaction
from tests.storage.fixtures import temporary_database


class TestQuestionHistory(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repository = QuestionRepository(self.database)
        self.cog = QuestionCog(MagicMock(), self.repository)

    async def test_unicode_keys_do_not_reuse_truncated_or_empty_history(self) -> None:
        await self.repository.answer(1, "", "unrecoverable")
        await self.repository.answer(1, "a", "truncated")
        for question in ("한국", "질문", "a한국", "🙂", "!", " ! "):
            with self.subTest(question=question):
                for candidate in (question, "replacement"):
                    self.cog.answers = [candidate]
                    request = interaction(user_id=1)
                    request.response.is_done.return_value = False
                    await self.cog.q._do_call(request, {"text": question})
                    request.response.send_message.assert_awaited_once_with(question)
        self.assertEqual(
            await self.repository.answer(1, "", "new"), ("unrecoverable", False)
        )

    async def test_slow_preferences_lock_and_sql_defer_then_edit(self) -> None:
        for boundary in ("preferences", "lock", "sql", "sql_error"):
            with self.subTest(boundary=boundary):
                await self._check_deferred_boundary(boundary)

    async def _check_deferred_boundary(self, boundary: str) -> None:
        request = interaction()
        request.response.is_done.return_value = False
        deferred = asyncio.Event()
        release = asyncio.Event()

        async def defer(**_kwargs: object) -> None:
            request.response.is_done.return_value = True
            deferred.set()

        async def preferences(
            item: Interaction, *, private: bool
        ) -> PresentationContext:
            await release.wait()
            return await original_context(item, private=private)

        original_context = interaction_context
        original_answer = self.repository.answer

        async def answer(
            user_id: int, question: str, candidate: str
        ) -> tuple[str, bool]:
            await release.wait()
            if boundary == "sql_error":
                raise OSError("unavailable")
            return await original_answer(user_id, question, candidate)

        request.response.defer.side_effect = defer
        queue_before = list(self.cog.answers)
        if boundary == "lock":
            await self.cog._answer_lock.acquire()
        with (
            patch.object(
                ask_cog,
                "interaction_context",
                side_effect=preferences
                if boundary == "preferences"
                else original_context,
            ),
            patch.object(
                self.repository,
                "answer",
                side_effect=answer if boundary.startswith("sql") else original_answer,
            ),
        ):
            # Override the explicit production budget only in this test.
            async def immediate_defer(
                item: Interaction,
                operation: Coroutine[object, object, str],
                *,
                defer_after: float,
            ) -> str:
                self.assertEqual(defer_after, 1.5)
                return await run_with_defer(item, operation, defer_after=0)

            with patch.object(ask_cog, "run_with_defer", side_effect=immediate_defer):
                task = asyncio.create_task(
                    self.cog.q._do_call(request, {"text": boundary})
                )
                await asyncio.wait_for(deferred.wait(), 2)
                release.set()
                if boundary == "lock":
                    self.cog._answer_lock.release()
                await self._assert_deferred_response(
                    task, request, queue_before, boundary == "sql_error"
                )
        request.response.send_message.assert_not_awaited()

    async def _assert_deferred_response(
        self,
        task: asyncio.Task[None],
        request: MagicMock,
        queue_before: list[str],
        failed: bool,
    ) -> None:
        if failed:
            with self.assertRaises(CommandInvokeError):
                await task
            self.assertEqual(self.cog.answers, queue_before)
            request.edit_original_response.assert_not_awaited()
        else:
            await task
            request.edit_original_response.assert_awaited_once()

    async def test_saved_translation_survives_language_change(
        self,
    ) -> None:
        preferences = PreferencesRepository(self.database)
        saved = await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.EN)
        )
        self.cog.answers = ["Yes!", "No!"]
        first = interaction()
        first.response.is_done.return_value = False
        first.client.preferences_repository = preferences
        await self.cog.q._do_call(first, {"text": "Question?"})
        first.response.send_message.assert_awaited_once_with("Yes!")
        await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.RU, revision=saved.revision)
        )
        second = interaction()
        second.response.is_done.return_value = False
        second.client.preferences_repository = preferences
        queue_before = list(self.cog.answers)
        await self.cog.q._do_call(second, {"text": "QUESTION ?"})
        second.response.send_message.assert_awaited_once_with("Yes!")
        self.assertEqual(self.cog.answers, queue_before)

    async def test_historical_russian_answer_is_verbatim_with_english_sources(
        self,
    ) -> None:
        request = interaction()
        request.response.is_done.return_value = False
        preferences = PreferencesRepository(self.database)
        await preferences.save(PreferenceScope.GUILD, 20, Preferences(Language.EN))
        request.client.preferences_repository = preferences
        historical = "Да! Именно так — старый ответ."
        await self.repository.answer(
            request.user.id, str_local("Question?"), historical
        )
        self.cog.answers = ["Yes!", "No!"]

        await self.cog.q._do_call(request, {"text": "QUESTION ?"})

        request.response.send_message.assert_awaited_once_with(historical)
        self.assertEqual(self.cog.answers, ["Yes!", "No!"])

    async def test_korean_answer_is_committed_and_survives_language_change(
        self,
    ) -> None:
        preferences = PreferencesRepository(self.database)
        saved = await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.KO)
        )
        self.cog.answers = ["Yes!", "No!"]
        first = interaction()
        first.response.is_done.return_value = False
        first.client.preferences_repository = preferences
        await self.cog.q._do_call(first, {"text": "Question?"})
        first.response.send_message.assert_awaited_once_with("네!")
        await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.EN, revision=saved.revision)
        )
        second = interaction()
        second.response.is_done.return_value = False
        second.client.preferences_repository = preferences
        queue_before = list(self.cog.answers)
        await self.cog.q._do_call(second, {"text": "QUESTION ?"})
        second.response.send_message.assert_awaited_once_with("네!")
        self.assertEqual(self.cog.answers, queue_before)

    async def test_returns_existing_answer_without_rewriting_it(self) -> None:
        for candidate in ("answer", "other"):
            self.cog.answers = [candidate]
            request = interaction(user_id=1)
            request.response.is_done.return_value = False
            await self.cog.q._do_call(request, {"text": "Question?"})
            request.response.send_message.assert_awaited_once_with("answer")
        self.assertEqual(self.cog.answers, ["other"])
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
        interaction.response.is_done.return_value = False
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
            item.response.is_done.return_value = False
        await asyncio.gather(
            *(self.cog.q._do_call(item, {"text": "Question?"}) for item in items)
        )
        for index, item in enumerate(items, start=1):
            answer, inserted = await self.repository.answer(
                index, str_local("Question?"), "must not replace"
            )
            self.assertFalse(inserted)
            item.response.send_message.assert_awaited_once_with(answer)


class TestQuestionKeys(unittest.TestCase):
    def test_ordinary_keys_and_canonical_unicode(self) -> None:
        for question, expected in (
            ("ПрИвЕт, Ёж! 123?", "приветёж123"),
            ("HELLO, world! 123", "helloworld123"),
            ("한국?", "한국"),
            ("E\u0301?", "é"),
            ("İ", "i\u0307"),
            ("किताब", "किताब"),
        ):
            with self.subTest(question=question):
                self.assertEqual(str_local(question), expected)
        self.assertNotEqual(str_local("한국"), str_local("질문"))
        self.assertNotEqual(str_local("ß"), str_local("ss"))
        self.assertNotEqual(str_local("１"), str_local("1"))

    def test_nontext_keys_preserve_exact_input(self) -> None:
        for question in ("", " ", "🙂", " 🙂", "🙂 ", "!", "!!", "\u0301"):
            with self.subTest(question=question):
                self.assertEqual(str_local(question), f"raw:v1:{question}")
