"""Greeting matching and response policy."""

import logging
import secrets
from collections.abc import Iterable, Sequence

from discord import Message
from discord.ext import commands
from rapidfuzz.process import extract
from rapidfuzz.utils import default_process

from stupidbot import settings as config
from stupidbot.features.access.operations import BlockManager
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import message_context

from .phrases import EVENING_ANSWERS, EVENING_QUEST, MORNING_ANSWERS, MORNING_QUEST

logger = logging.getLogger(__name__)


class Greetings:
    """Match greeting phrases after prefix and access checks."""

    def __init__(self, bot: commands.Bot, block_manager: BlockManager) -> None:
        self.bot = bot
        self.block_manager = block_manager

    async def handle_message(self, message: Message) -> None:
        if message.author.bot:
            return
        prefix = await self.bot.get_prefix(message)
        prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
        if message.content.startswith(prefixes):
            return
        if message.guild and await self.block_manager.is_user_blocked(
            message.guild.id, message.author.id
        ):
            return
        try:
            await self.quest_process_message(message)
        except Exception:
            logger.exception("Failed to process message %s", message.id)

    async def quest_process_message(self, message: Message) -> None:
        if len(message.content) < 5:
            return
        res = self.process_fuzzy_message(message, MORNING_QUEST, MORNING_ANSWERS)
        if res:
            await self._send_answer(message, res)
            return
        res = self.process_fuzzy_message(message, EVENING_QUEST, EVENING_ANSWERS)
        if res:
            await self._send_answer(message, res)

    async def _send_answer(self, message: Message, answer: str) -> None:
        context = await message_context(self.bot, message)
        text = (
            answer
            if answer.startswith("https://")
            else catalog("greetings", context.language).pgettext("answer", answer)
        )
        await message.channel.send(text)

    def process_fuzzy_message(
        self,
        message: Message,
        quests: Iterable[str],
        answers: Sequence[str],
        threshold: int = config.FUZZY_THRESHOLD_DEFAULT,
    ) -> str | None:
        """Return a random answer when a message fuzzy-matches a known phrase.

        Args:
            message: Message whose content is compared.
            quests: Candidate phrases.
            answers: Non-empty answer choices.
            threshold: Minimum fuzzy-match score.

        Returns:
            A random answer when a candidate meets the threshold, otherwise
            ``None``.
        """
        fuzzy_results = extract(
            message.content,
            quests,
            limit=config.FUZZY_MATCH_LIMIT,
            processor=default_process,
        )
        _, best_score, _ = max(fuzzy_results, key=lambda x: x[1])
        if best_score >= threshold:
            logger.info(
                "Fuzzy response matched: %s",
                {
                    "message_id": message.id,
                    "guild_id": message.guild.id if message.guild else None,
                    "channel_id": message.channel.id,
                    "content": message.content,
                    "score": best_score,
                },
            )
            return secrets.choice(answers or [None])
        return None
