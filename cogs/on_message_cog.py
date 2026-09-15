"""Automatic message responses based on fuzzy matching.

Listens to messages and responds to greetings.
"""

import logging
import secrets
from collections.abc import Iterable, Sequence

from discord import Message
from discord.ext import commands
from rapidfuzz.process import extract
from rapidfuzz.utils import default_process

import config
from api.blocking import block_manager
from resources import EVENING_ANSWERS, EVENING_QUEST, MORNING_ANSWERS, MORNING_QUEST

logger = logging.getLogger(__name__)


class OnMessageCog(commands.Cog):
    """Log, auto-respond to greetings and common phrases."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        self._log_message(message)
        if message.author.bot:
            return
        if message.content.startswith(tuple(await self.bot.get_prefix(message))):
            return
        if message.guild and await block_manager.is_user_blocked(
            message.guild.id, message.author.id
        ):
            return
        try:
            await self.quest_process_message(message)
        except Exception:
            logger.exception("Failed to process message %s", message.id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message) -> None:
        changes: list[str] = []
        before_flags: list[str] | None = None
        after_flags: list[str] | None = None

        attr_whitelist = [
            "content",
            "embeds",
            "attachments",
            "stickers",
            "components",
            "pinned",
            "reference",
        ]

        for attr in attr_whitelist:
            before_val = getattr(before, attr, None)
            after_val = getattr(after, attr, None)

            if before_val != after_val:
                changes.append(attr)

        if before.flags.value != after.flags.value:
            before_flags = [name for name, value in before.flags if value]
            after_flags = [name for name, value in after.flags if value]
            changes.append("flags")

        if changes:
            logger.debug(
                "Message edited: %s",
                {
                    "message_id": after.id,
                    "guild_id": after.guild.id if after.guild else None,
                    "channel_id": after.channel.id,
                    "author_id": after.author.id,
                    "fields": changes,
                    "before_flags": before_flags,
                    "after_flags": after_flags,
                },
            )

            self._log_message(after, is_edit=True)

    async def quest_process_message(self, message: Message) -> None:
        if len(message.content) < 5:
            return
        res = self.process_fuzzy_message(message, MORNING_QUEST, MORNING_ANSWERS)
        if res:
            await message.channel.send(res)
            return

        res = self.process_fuzzy_message(message, EVENING_QUEST, EVENING_ANSWERS)
        if res:
            await message.channel.send(res)

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
            A repeatable answer when a candidate meets the threshold, otherwise
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

    def _log_message(self, message: Message, *, is_edit: bool = False) -> None:
        """Log the complete Discord message payload used for later analysis."""
        payload = {
            "status": "edited" if is_edit else "new",
            "message_id": message.id,
            "guild_id": message.guild.id if message.guild else None,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author": str(message.author),
            "content": message.content,
            "attachments": [attachment.to_dict() for attachment in message.attachments],
            "embeds": [embed.to_dict() for embed in message.embeds],
            "stickers": [
                {
                    "id": sticker.id,
                    "name": sticker.name,
                    "format": sticker.format.name,
                    "url": sticker.url,
                }
                for sticker in message.stickers
            ],
            "components": [component.to_dict() for component in message.components],
            "reference": (
                message.reference.to_dict() if message.reference is not None else None
            ),
            "poll": repr(message.poll) if message.poll is not None else None,
            "flags": message.flags.value,
        }
        logger.info("Discord message: %s", payload)


async def setup(bot: commands.Bot) -> None:
    """Register the message-listener cog."""
    await bot.add_cog(OnMessageCog(bot))
