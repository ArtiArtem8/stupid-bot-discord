"""Magic 8-ball style question answering system.

Provides a `/ask` command that gives random answers to user questions,
with answer history tracking to prevent duplicate questions.
"""

import asyncio
import logging
import secrets

from discord import Interaction, app_commands
from discord.ext import commands

import config
from framework.base_cog import BaseCog
from resources import CAPABILITIES
from utils.json_store import AsyncJsonFileStore
from utils.json_types import JsonObject
from utils.text_utils import random_answer, str_local

logger = logging.getLogger(__name__)


class QuestionCog(BaseCog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.answers = secrets.SystemRandom().sample(
            CAPABILITIES, min(len(CAPABILITIES), config.MAX_ANSWER_SAMPLE_SIZE)
        )
        self._answer_lock = asyncio.Lock()
        self._history_store = AsyncJsonFileStore(config.ANSWER_FILE, backup_amount=2)
        logger.info("Initialized /ask answer queue size=%s", len(self.answers))

    @app_commands.command(
        name="ask",
        description="Магический шар, задай любой вопрос",
    )
    async def q(self, interaction: Interaction, *, text: str) -> None:
        logger.info(
            "/ask invoked user=%s user_id=%s question=%r",
            interaction.user,
            interaction.user.id,
            text,
        )
        generated = False

        async with self._answer_lock:
            prev_message = await self._add_to_history(
                str(interaction.user.id),
                text,
                self.answers[0],
            )

            if prev_message is not None:
                reply = prev_message
            else:
                self.answers.append(random_answer(text, answers=CAPABILITIES))
                reply = self.answers.pop(0)
                generated = True

        if prev_message is not None:
            logger.info(
                "/ask resolved user_id=%s result=cached question=%r answer=%r",
                interaction.user.id,
                text,
                reply,
            )
        elif generated:
            logger.info(
                "/ask invoked user=%s user_id=%s question=%r",
                interaction.user,
                interaction.user.id,
                text,
            )

        await interaction.response.send_message(reply)

    async def _add_to_history(
        self, user_id: str, question: str, answer: str
    ) -> str | None:
        """Store one normalized question without overwriting an existing answer.

        The history store serializes the read-modify-write operation for this cog
        instance. The existing answer is returned when the same user already asked
        the normalized question; otherwise the new answer is persisted and
        ``None`` is returned.
        """
        filtered_text = str_local(question)
        existing_answer: str | None = None

        def _updater(data: JsonObject) -> None:
            nonlocal existing_answer
            user_history = data.get(user_id)
            if user_history is None:
                user_history = {}
                data[user_id] = user_history
            elif not isinstance(user_history, dict):
                raise ValueError("Question history has an invalid user record")

            existing = user_history.get(filtered_text)
            if isinstance(existing, str):
                existing_answer = existing
                return
            if existing is not None:
                raise ValueError("Question history has an invalid answer record")
            user_history[filtered_text] = answer

        await self._history_store.update(_updater)
        return existing_answer


async def setup(bot: commands.Bot) -> None:
    """Register the question cog."""
    await bot.add_cog(QuestionCog(bot))
