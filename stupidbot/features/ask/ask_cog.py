"""Magic 8-ball style question answering system.

Provides a `/ask` command that gives random answers to user questions,
with answer history tracking to prevent duplicate questions.
"""

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING

from discord import Interaction, app_commands
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.features.ask.answers import CAPABILITIES, random_answer, str_local
from stupidbot.features.ask.repository import QuestionRepository

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


class QuestionCog(BaseCog):
    def __init__(self, bot: commands.Bot, repository: QuestionRepository) -> None:
        super().__init__(bot)
        self.answers = secrets.SystemRandom().sample(
            CAPABILITIES, min(len(CAPABILITIES), config.MAX_ANSWER_SAMPLE_SIZE)
        )
        self._answer_lock = asyncio.Lock()
        self._repository = repository
        logger.info("Initial /ask answers: %s", self.answers)

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
        async with self._answer_lock:
            prev_message = await self._add_to_history(
                interaction.user.id,
                text,
                self.answers[0],
            )

            if prev_message is not None:
                reply = prev_message
            else:
                self.answers.append(random_answer(text, answers=CAPABILITIES))
                reply = self.answers.pop(0)

        if prev_message is not None:
            logger.info(
                "/ask resolved user_id=%s result=cached question=%r answer=%r",
                interaction.user.id,
                text,
                reply,
            )
        else:
            logger.info(
                "/ask resolved user_id=%s result=new question=%r answer=%r",
                interaction.user.id,
                text,
                reply,
            )

        await interaction.response.send_message(reply)

    async def _add_to_history(
        self, user_id: int, question: str, answer: str
    ) -> str | None:
        """Return the committed winner without advancing the queue on failed storage."""
        committed, inserted = await self._repository.answer(
            user_id, str_local(question), answer
        )
        return None if inserted else committed


async def setup(bot: "StupidBot") -> None:
    """Register the question cog."""
    await bot.add_cog(QuestionCog(bot, bot.question_repository))
