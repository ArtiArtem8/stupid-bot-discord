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
from framework import BaseCog
from resources import CAPABILITIES
from utils import AsyncJsonFileStore, random_answer, str_local
from utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class QuestionCog(BaseCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        # predictions
        self.answers = secrets.SystemRandom().sample(
            CAPABILITIES, min(len(CAPABILITIES), config.MAX_ANSWER_SAMPLE_SIZE)
        )
        self._answer_lock = asyncio.Lock()
        self._history_store = AsyncJsonFileStore(config.ANSWER_FILE, backup_amount=2)
        logger.info("Initial /ask answers: %s", self.answers)

    @app_commands.command(
        name="ask",
        description="Магический шар, задай любой вопрос",
    )
    async def q(self, interaction: Interaction, *, text: str):
        logger.info(
            "User %s(%s) asked: %s",
            interaction.user,
            interaction.user.id,
            text,
        )
        queue_preview: tuple[list[str], list[str]] | None = None

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
                queue_preview = (self.answers[:2], self.answers[-2:])

        if prev_message is not None:
            logger.info("User already asked: %s -> %s", text, prev_message)
        elif queue_preview is not None:
            logger.info(
                "%s -> %s...%s",
                reply,
                queue_preview[0],
                queue_preview[1],
            )

        await interaction.response.send_message(reply)

    async def _add_to_history(
        self, user_id: str, question: str, answer: str
    ) -> str | None:
        """Add a question to the global answers.

        Args:
            user_id: The ID of the user who asked the question.
            question: The question asked.
            answer: The answer to the question.

        Returns:
            The existing answer if the user already asked the question, None otherwise.

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


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(QuestionCog(bot))
