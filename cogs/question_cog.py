"""Magic 8-ball style question answering system.

Provides a `/ask` command that gives random answers to user questions,
with answer history tracking to prevent duplicate questions.
"""

import logging
import secrets

from discord import Interaction, app_commands
from discord.ext import commands

from config import ANSWER_FILE, CAPABILITIES
from utils import BaseCog, get_json, random_answer, save_json, str_local

MAX_SAMPLE_SIZE = 8
"""Maximum number of answers to sample for rotation"""


class QuestionCog(BaseCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.logger = logging.getLogger("QuestionCog")
        # predictions
        self.answers = secrets.SystemRandom().sample(
            CAPABILITIES, min(len(CAPABILITIES), MAX_SAMPLE_SIZE)
        )
        self.logger.info("Initial /ask answers: %s", self.answers)

    @app_commands.command(
        name="ask",
        description="Магический шар, задай любой вопрос",
    )
    async def q(self, interaction: Interaction, *, text: str):
        self.logger.info(
            "User %s(%s) asked: %s", interaction.user, interaction.user.id, text
        )
        prev_message = self._add_to_history(
            str(interaction.user.id), text, self.answers[0]
        )
        if prev_message:
            self.logger.info("User already asked: %s -> %s", text, prev_message)
            await interaction.response.send_message(prev_message)
            return

        self.answers.append(random_answer(text, answers=CAPABILITIES))
        reply = self.answers.pop(0)
        self.logger.info(f"{reply} -> {self.answers[:2]}...{self.answers[-2:]}")

        await interaction.response.send_message(reply)

    def _add_to_history(self, user_id: str, question: str, answer: str) -> str | None:
        """Add a question to the global answers.

        Args:
            user_id: The ID of the user who asked the question.
            question: The question asked.
            answer: The answer to the question.

        Returns:
            The existing answer if the user already asked the question, None otherwise.

        """
        data = get_json(ANSWER_FILE) or {}
        filtered_text = str_local(question)
        if existing := data.get(user_id, {}).get(filtered_text, None):
            return existing

        data.setdefault(user_id, {})[filtered_text] = answer
        save_json(ANSWER_FILE, data, backup_amount=2)
        return None


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(QuestionCog(bot))
