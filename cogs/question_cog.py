import logging
from random import choices

from discord import Interaction, app_commands
from discord.ext import commands

from config import ANSWER_FILE, CAPABILITIES
from utils import BlockManager, get_json, random_answer, save_json, str_local


class QuestionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        # predictions
        self.bot = bot
        self.logger = logging.getLogger("QuestionCog")
        self.answers = choices(CAPABILITIES, k=8)
        self.logger.info(f"Next {self.bot.command_prefix}q answers: %s", self.answers)

    async def interaction_check(self, interaction: Interaction):  # type: ignore
        check = super().interaction_check(interaction)
        if interaction.guild and BlockManager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            await interaction.response.send_message(
                "⛔ Доступ к командам запрещён.", ephemeral=True
            )
            self.logger.info(f"User {interaction.user} is blocked.")
            return False

        return check

    @app_commands.command(
        name="ask",
        description="Магический шар, задай любой вопрос",
    )
    async def q(self, interaction: Interaction, *, text: str):
        self.logger.info(
            "User %s(%s) asked: %s", interaction.user, interaction.user.id, text
        )
        prev_message = self._add_to_global_answer(
            str(interaction.user.id), text, self.answers[0]
        )
        if prev_message:
            self.logger.info("User already asked: %s -> %s", text, prev_message)
            await interaction.response.send_message(prev_message)
            return

        self.answers.append(random_answer(text, answers=CAPABILITIES))
        gock = self.answers.pop(0)
        self.logger.info(f"{gock} -> {self.answers[:2]}...{self.answers[-2:]}")

        await interaction.response.send_message(gock)

    def _add_to_global_answer(
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
        data = get_json(ANSWER_FILE)
        filtered_text = str_local(question)
        if data is None:
            data = {}
        if existing := data.get(user_id, {}).get(filtered_text, None):
            return existing

        data.setdefault(user_id, {})[filtered_text] = answer
        save_json(ANSWER_FILE, data, backup_amount=2)
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(QuestionCog(bot))
