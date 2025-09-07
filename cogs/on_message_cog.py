import logging
from random import choice

from discord import Message
from discord.ext import commands
from fuzzywuzzy.process import extract  # type: ignore

from config import EVENING_ANSWERS, EVENING_QUEST, MORNING_ANSWERS, MORNING_QUEST
from utils import BlockManager


class OnMessageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("OnMessageCog")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        self.log_message(message)
        if message.author.bot:
            return
        if message.content.startswith(tuple(await self.bot.get_prefix(message))):
            return
        if message.guild and BlockManager.is_user_blocked(
            message.guild.id, message.author.id
        ):
            return
        try:
            await self.quest_process_message(message)
        except Exception as e:
            self.logger.error("Failed to process message %s: %s", message.content, e)

    async def quest_process_message(self, message: Message):
        if len(message.content) >= 5:
            res = self.process_fuzzy_message(message, MORNING_QUEST, MORNING_ANSWERS)
            if res is not None:
                return await message.channel.send(res)

            res = self.process_fuzzy_message(message, EVENING_QUEST, EVENING_ANSWERS)
            if res is not None:
                return await message.channel.send(res)

    def process_fuzzy_message(
        self,
        message: Message,
        quests: list[str],
        answers: list[str],
        threshold: int = 95,
    ) -> str | None:
        """Process a message to check if it matches one of the given "quests" and
        return a random answer if it does.

        Args:
            message: The message to process.
            quests: A sequence of strings to match against the message content.
            answers: A sequence of strings to return if the message matches.
            threshold: The minimum score required for a match.

        Returns:
            A random answer if the message matches, None otherwise.

        """
        fuzzy_results: list[tuple[str, int]] = extract(  # type: ignore
            message.content, quests, limit=10
        )
        _, best_score = max(fuzzy_results, key=lambda x: x[1])

        if best_score >= threshold:
            self.logger.info(
                '%s processed with message: "%s" in %s from %s',
                str(fuzzy_results[:3]),
                message.content,
                message.channel,
                message.author,
            )
            return choice(answers)

        return None

    def log_message(self, message: Message):
        self.logger.info(
            '%s sended - "%s" in %s', message.author, message.content, message.channel
        )
        attachments = tuple(
            ((x.url, x.content_type, x.filename) for x in message.attachments)
        )
        if attachments:
            self.logger.info("Attachments: %s", attachments)


async def setup(bot: commands.Bot):
    """Setup.

    :param commands.Bot bot: BOT ITSELF
    """
    await bot.add_cog(OnMessageCog(bot))
