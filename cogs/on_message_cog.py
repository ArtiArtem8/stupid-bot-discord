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
                "".join(
                    (
                        "Message edited message_id=%s guild_id=%s channel_id=%s ",
                        "author_id=%s fields=%s before_flags=%s after_flags=%s",
                    )
                ),
                after.id,
                after.guild.id if after.guild else None,
                after.channel.id,
                after.author.id,
                changes,
                before_flags,
                after_flags,
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
                "".join(
                    (
                        "Fuzzy response matched message_id=%s guild_id=%s ",
                        "channel_id=%s content_length=%s score=%s",
                    )
                ),
                message.id,
                message.guild.id if message.guild else None,
                message.channel.id,
                len(message.content),
                best_score,
            )

            return secrets.choice(answers or [None])
        return None

    def _log_message(self, message: Message, *, is_edit: bool = False) -> None:
        """Log bounded message metadata without retaining user content."""
        logger.info(
            "".join(
                (
                    "Message %s message_id=%s guild_id=%s channel_id=%s author_id=%s ",
                    "content_length=%s attachment_count=%s embed_count=%s ",
                    "sticker_count=%s component_count=%s has_reference=%s has_poll=%s",
                )
            ),
            "edited" if is_edit else "received",
            message.id,
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
            len(message.content),
            len(message.attachments),
            len(message.embeds),
            len(message.stickers),
            len(message.components),
            message.reference is not None,
            message.poll is not None,
        )


async def setup(bot: commands.Bot) -> None:
    """Register the message-listener cog."""
    await bot.add_cog(OnMessageCog(bot))
