"""Question history returns the committed winner and protects the RAM answer queue."""

import asyncio
import unittest
from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import Interaction
from sqlalchemy import select

from stupidbot import settings as config
from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.feedback import FeedbackUI
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
        self.bot = StupidBot()
        self.addAsyncCleanup(self.bot.close)
        self.bot.preferences_repository = PreferencesRepository(self.database)
        self.enterContext(patch.object(self.bot, "dispatch", MagicMock()))
        self.enterContext(
            patch.object(self.bot.block_manager, "is_user_blocked", return_value=False)
        )
        self.cog = QuestionCog(self.bot, self.repository)
        self.bot.tree.add_command(self.cog.q)

    def _request(self, user_id: int = 10) -> MagicMock:
        request = interaction(user_id=user_id)
        request._state = self.bot._connection
        request.client = self.bot
        request.guild = MagicMock(spec=discord.Guild, id=20, name="Guild")
        request.created_at = datetime.now(UTC)
        request.type = discord.InteractionType.application_command
        request.command = self.cog.q
        request.command_failed = False
        request.response.type = None
        request.response.is_done.return_value = False

        async def defer(*, thinking: bool, ephemeral: bool) -> None:
            self.assertTrue(thinking)
            self.assertFalse(ephemeral)
            request.response.is_done.return_value = True
            request.response.type = (
                discord.InteractionResponseType.deferred_channel_message
            )

        request.response.defer.side_effect = defer
        return request

    async def _dispatch(self, request: MagicMock, text: str) -> None:
        request.data = {
            "name": "ask",
            "type": 1,
            "options": [{"name": "text", "type": 3, "value": text}],
        }
        await self.bot.tree._call(request)

    async def test_unicode_keys_do_not_reuse_truncated_or_empty_history(self) -> None:
        await self.repository.answer(1, "", "unrecoverable")
        await self.repository.answer(1, "a", "truncated")
        for question in ("한국", "질문", "a한국", "🙂", "!", " ! "):
            with self.subTest(question=question):
                for candidate in (question, "replacement"):
                    self.cog.answers = [candidate]
                    request = self._request(user_id=1)
                    await self._dispatch(request, question)
                    request.edit_original_response.assert_awaited_once_with(
                        content=question
                    )
        self.assertEqual(
            await self.repository.answer(1, "", "new"), ("unrecoverable", False)
        )

    async def test_registered_command_acknowledges_before_preferences_lock_and_sql(
        self,
    ) -> None:
        for boundary in ("preferences", "lock", "sql"):
            with self.subTest(boundary=boundary):
                await self._check_deferred_boundary(boundary)

    async def _check_deferred_boundary(self, boundary: str) -> None:
        request = self._request()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_answer = self.repository.answer

        async def preferences(
            item: Interaction, *, private: bool
        ) -> PresentationContext:
            request.response.defer.assert_awaited_once_with(
                thinking=True, ephemeral=False
            )
            if boundary in {"preferences", "lock"}:
                entered.set()
            if boundary == "preferences":
                await release.wait()
            return await interaction_context(item, private=private)

        async def answer(
            user_id: int, question: str, candidate: str
        ) -> tuple[str, bool]:
            request.response.defer.assert_awaited_once_with(
                thinking=True, ephemeral=False
            )
            entered.set()
            await release.wait()
            return await original_answer(user_id, question, candidate)

        if boundary == "lock":
            await self.cog._answer_lock.acquire()
        with (
            patch.object(ask_cog, "interaction_context", side_effect=preferences),
            patch.object(self.repository, "answer", side_effect=answer),
        ):
            task = asyncio.create_task(self._dispatch(request, boundary))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(task.done())
            finally:
                release.set()
                if boundary == "lock":
                    self.cog._answer_lock.release()
                await task
            self.assertFalse(request.command_failed)
            request.edit_original_response.assert_awaited_once()
        request.response.send_message.assert_not_awaited()

    async def test_registered_cancellation_drains_answer_without_advancing_queue(
        self,
    ) -> None:
        request = self._request()
        entered = asyncio.Event()
        finalized = asyncio.Event()
        queue_before = list(self.cog.answers)

        async def answer(
            _user_id: int, _question: str, _candidate: str
        ) -> tuple[str, bool]:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()
            return "unused", False

        with patch.object(self.repository, "answer", side_effect=answer):
            task = asyncio.create_task(self._dispatch(request, "Question?"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                request.response.defer.assert_awaited_once_with(
                    thinking=True, ephemeral=False
                )
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(finalized.is_set())
        self.assertEqual(self.cog.answers, queue_before)
        request.edit_original_response.assert_not_awaited()
        request.response.send_message.assert_not_awaited()

    async def test_registered_command_ack_failure_does_not_start_answer(self) -> None:
        request = self._request()
        request.response.defer.side_effect = discord.HTTPException(
            MagicMock(status=500, reason="unavailable"), "defer failed"
        )
        queue_before = list(self.cog.answers)
        with (
            patch.object(self.repository, "answer", new=AsyncMock()) as answer,
            self.assertLogs("stupidbot.discord_ui.errors", level="WARNING"),
        ):
            await self._dispatch(request, "Question?")
        answer.assert_not_awaited()
        self.assertEqual(self.cog.answers, queue_before)
        request.response.send_message.assert_not_awaited()
        request.edit_original_response.assert_not_awaited()

    async def test_saved_translation_survives_language_change(
        self,
    ) -> None:
        preferences = PreferencesRepository(self.database)
        saved = await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.EN)
        )
        self.cog.answers = ["Yes!", "No!"]
        first = self._request()
        first.client.preferences_repository = preferences
        await self._dispatch(first, "Question?")
        first.edit_original_response.assert_awaited_once_with(content="Yes!")
        await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.RU, revision=saved.revision)
        )
        second = self._request()
        second.client.preferences_repository = preferences
        queue_before = list(self.cog.answers)
        await self._dispatch(second, "QUESTION ?")
        second.edit_original_response.assert_awaited_once_with(content="Yes!")
        self.assertEqual(self.cog.answers, queue_before)

    async def test_historical_russian_answer_is_verbatim_with_english_sources(
        self,
    ) -> None:
        request = self._request()
        preferences = PreferencesRepository(self.database)
        await preferences.save(PreferenceScope.GUILD, 20, Preferences(Language.EN))
        request.client.preferences_repository = preferences
        historical = "Да! Именно так — старый ответ."
        await self.repository.answer(
            request.user.id, str_local("Question?"), historical
        )
        self.cog.answers = ["Yes!", "No!"]

        await self._dispatch(request, "QUESTION ?")

        request.edit_original_response.assert_awaited_once_with(content=historical)
        self.assertEqual(self.cog.answers, ["Yes!", "No!"])

    async def test_korean_answer_is_committed_and_survives_language_change(
        self,
    ) -> None:
        preferences = PreferencesRepository(self.database)
        saved = await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.KO)
        )
        self.cog.answers = ["Yes!", "No!"]
        first = self._request()
        first.client.preferences_repository = preferences
        await self._dispatch(first, "Question?")
        first.edit_original_response.assert_awaited_once_with(content="네!")
        await preferences.save(
            PreferenceScope.GUILD, 20, Preferences(Language.EN, revision=saved.revision)
        )
        second = self._request()
        second.client.preferences_repository = preferences
        queue_before = list(self.cog.answers)
        await self._dispatch(second, "QUESTION ?")
        second.edit_original_response.assert_awaited_once_with(content="네!")
        self.assertEqual(self.cog.answers, queue_before)

    async def test_returns_existing_answer_without_rewriting_it(self) -> None:
        for candidate in ("answer", "other"):
            self.cog.answers = [candidate]
            request = self._request(user_id=1)
            await self._dispatch(request, "Question?")
            request.edit_original_response.assert_awaited_once_with(content="answer")
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

    async def test_payload_logging_is_opt_in_for_new_and_cached_answers(self) -> None:
        question = "Private question marker?"
        reply = "Private answer marker"
        username = "Private username marker"
        for enabled in (False, True):
            user_id = 10 + int(enabled)
            self.cog.answers = [reply, "next"]
            for result in ("new", "cached"):
                with self.subTest(logging=enabled, result=result):
                    request = self._request(user_id)
                    request.user.name = username
                    request.user.global_name = username
                    request.user.__str__.return_value = username
                    with (
                        patch.object(config, "MESSAGE_LOG_ENABLED", enabled),
                        self.assertLogs("stupidbot", level="DEBUG") as captured,
                    ):
                        await self._dispatch(request, question)
                    request.edit_original_response.assert_awaited_once_with(
                        content=reply
                    )
                    self.assertEqual(
                        await self.repository.answer(
                            user_id, str_local(question), "must not replace"
                        ),
                        (reply, False),
                    )
                    records = [
                        record.getMessage()
                        for record in captured.records
                        if record.name == ask_cog.logger.name
                    ]
                    expected = f"/ask resolved user_id={user_id} result={result}"
                    if enabled:
                        expected = f"{expected} question={question!r} answer={reply!r}"
                    else:
                        output = "\n".join(captured.output)
                        self.assertNotIn(question, output)
                        self.assertNotIn(reply, output)
                    self.assertEqual(records, [expected])
                    self.assertNotIn(username, records[0])

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

    async def test_failed_persistence_does_not_advance_queue(self) -> None:
        self.cog.answers = ["first", "second"]
        request = self._request(user_id=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fail(
            _user_id: int, _question: str, _candidate: str
        ) -> tuple[str, bool]:
            entered.set()
            await release.wait()
            raise OSError("unavailable")

        with (
            patch.object(self.repository, "answer", side_effect=fail),
            patch.object(FeedbackUI, "send", new=AsyncMock()) as feedback,
            self.assertLogs("stupidbot.discord_ui.errors", level="ERROR"),
        ):
            task = asyncio.create_task(self._dispatch(request, "Question?"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                request.response.defer.assert_awaited_once_with(
                    thinking=True, ephemeral=False
                )
                self.assertFalse(task.done())
            finally:
                release.set()
                await task
        self.assertTrue(request.command_failed)
        self.assertEqual(self.cog.answers, ["first", "second"])
        feedback.assert_awaited_once()
        request.edit_original_response.assert_not_awaited()

    async def test_concurrent_questions_send_the_answer_they_commit(self) -> None:
        self.cog.answers = ["first", "second"]
        items = [self._request(user_id=index) for index in (1, 2)]
        await asyncio.gather(*(self._dispatch(item, "Question?") for item in items))
        for index, item in enumerate(items, start=1):
            answer, inserted = await self.repository.answer(
                index, str_local("Question?"), "must not replace"
            )
            self.assertFalse(inserted)
            item.edit_original_response.assert_awaited_once_with(content=answer)


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
