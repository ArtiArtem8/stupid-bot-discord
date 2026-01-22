"""Automatic message responses based on fuzzy matching.

Listens to messages and responds to greetings.
"""

import logging
import secrets
from collections.abc import Callable, Iterable, Sequence, Sized

from discord import Message
from discord.ext import commands
from rapidfuzz.process import extract
from rapidfuzz.utils import default_process

import config
from api import block_manager
from resources import EVENING_ANSWERS, EVENING_QUEST, MORNING_ANSWERS, MORNING_QUEST
from utils import truncate_text

logger = logging.getLogger(__name__)


class OnMessageCog(commands.Cog):
    """Log, auto-respond to greetings and common phrases."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
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
        except Exception as e:
            logger.error("Failed to process message %s: %s", message.content, e)

    def _format_change(self, attr: str, before: object, after: object) -> str:
        """Smart diff formatting by type."""
        if type(before) is not type(after):
            return (
                f"{attr} (type changed): "
                f"{type(before).__name__} -> {type(after).__name__}"
            )
        elif isinstance(before, str) and isinstance(after, str):
            return (
                f"{attr}: '{truncate_text(before, 100, mode='middle')}'"
                f" -> '{truncate_text(after, 100, mode='middle')}'"
            )
        elif isinstance(before, Sized) and isinstance(after, Sized):
            return f"{attr}: {len(before)} -> {len(after)}"
        elif isinstance(before, bool):
            return f"{attr}: {before} -> {after}"
        else:
            before_summ = "exists" if before else "None"
            after_summ = "exists" if after else "None"
            return f"{attr}: {before_summ} -> {after_summ}"

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        changes: list[str] = []

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
                changes.append(self._format_change(attr, before_val, after_val))

        if before.flags.value != after.flags.value:
            before_flags = [name for name, value in before.flags if value]
            after_flags = [name for name, value in after.flags if value]
            changes.append(f"flags: {before_flags} -> {after_flags}")

        if changes:
            logger.debug(
                "Message edited by %s in %s | Changes: %s",
                after.author,
                after.channel,
                ", ".join(changes),
            )

            self._log_message(after, is_edit=True)

    async def quest_process_message(self, message: Message):
        if len(message.content) < 5:
            return
        res = self.process_fuzzy_message(message, MORNING_QUEST, MORNING_ANSWERS)
        if res:
            return await message.channel.send(res)

        res = self.process_fuzzy_message(message, EVENING_QUEST, EVENING_ANSWERS)
        if res:
            return await message.channel.send(res)

    def process_fuzzy_message(
        self,
        message: Message,
        quests: Iterable[str],
        answers: Sequence[str],
        threshold: int = config.FUZZY_THRESHOLD_DEFAULT,
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
        fuzzy_results = extract(
            message.content,
            quests,
            limit=config.FUZZY_MATCH_LIMIT,
            processor=default_process,
        )
        _, best_score, _ = max(fuzzy_results, key=lambda x: x[1])

        if best_score >= threshold:
            logger.info(
                '%s processed with message: "%s" in %s from %s',
                str(fuzzy_results[:3]),
                message.content,
                message.channel,
                message.author,
            )

            return secrets.choice(answers or [None])
        return None

    def _log_section[T](
        self,
        label: str,
        data: T | None,
        summary_factory: Callable[[T], object],
        debug_factory: Callable[[T], object],
    ) -> None:
        """Helper to log a section with INFO summary and lazy DEBUG details.

        Args:
            self: Self with logger.
            label: The log label (e.g., "Attachments").
            data: The object to check for truthiness before logging.
            summary_factory: Lambda returning the lightweight INFO payload.
            debug_factory: Lambda returning the expensive DEBUG payload.

        """
        if not data:
            return
        if log_data := summary_factory(data):
            logger.info(f"{label}: %s", log_data)
        if logger.isEnabledFor(logging.DEBUG) and (log_data := debug_factory(data)):
            logger.debug(f"Full {label.lower()}: %s", log_data)

    def _log_message(self, message: Message, *, is_edit: bool = False) -> None:
        """Log message with structured INFO summaries and lazy DEBUG details."""
        content_flags: dict[str, object] = {
            "attachments": message.attachments,
            "embeds": message.embeds,
            "stickers": message.stickers,
            "components": message.components,
            "reference": message.reference,
            "poll": message.poll,
        }
        if not is_edit:
            logger.info(
                '%s sent - "%s" in %s (%s)',
                message.author,
                message.content,
                message.channel,
                ", ".join(k for k, v in content_flags.items() if v),
            )

        self._log_section(
            "Attachments",
            message.attachments,
            summary_factory=lambda x: [(a.content_type, a.url) for a in x],
            debug_factory=lambda x: {f"att_{i}": a.to_dict() for i, a in enumerate(x)},
        )

        self._log_section(
            "Embeds",
            message.embeds,
            summary_factory=lambda _: "",
            debug_factory=lambda x: {
                f"embed_{i}": e.to_dict() for i, e in enumerate(x)
            },
        )

        self._log_section(
            "Stickers",
            message.stickers,
            summary_factory=lambda _: "",
            debug_factory=lambda x: [(s.id, s.name, s.format.name, s.url) for s in x],
        )

        self._log_section(
            "Reference",
            message.reference,
            summary_factory=lambda x: {
                "message_id": x.message_id,
                "channel_id": x.channel_id,
                "guild_id": x.guild_id,
            },
            debug_factory=lambda x: x.to_dict(),
        )

        self._log_section(
            "Components",
            message.components,
            summary_factory=lambda _: "",
            debug_factory=lambda x: {
                f"component_{i}": c.to_dict() for i, c in enumerate(x)
            },
        )

        self._log_section(
            "Poll",
            message.poll,
            summary_factory=lambda x: {
                "question": x.question,
                "options_count": len(x.answers),
            },
            debug_factory=lambda x: getattr(x, "_to_dict", repr(x)),
        )

        self._log_section(
            "Flags",
            message.flags.value,
            summary_factory=lambda _: "",
            debug_factory=lambda x: f"{x} (0x{x:x})",
        )


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(OnMessageCog(bot))
