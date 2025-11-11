"""Automatic message responses based on fuzzy matching.

Listens to messages and responds to greetings.
"""

import logging
import secrets
from typing import Any

from discord import Message
from discord.ext import commands
from fuzzywuzzy.process import extract  # type: ignore

from config import EVENING_ANSWERS, EVENING_QUEST, MORNING_ANSWERS, MORNING_QUEST
from utils import block_manager

DEFAULT_FUZZY_THRESHOLD = 95
"""Minimum fuzzy match score (0-100) to trigger response"""

FUZZY_MATCH_LIMIT = 10
"""Maximum number of fuzzy matches to evaluate"""


class OnMessageCog(commands.Cog):
    """Log, auto-respond to greetings and common phrases."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("OnMessageCog")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        self._log_message(message)
        if message.author.bot:
            return
        if message.content.startswith(tuple(await self.bot.get_prefix(message))):
            return
        if message.guild and block_manager.is_user_blocked(
            message.guild.id, message.author.id
        ):
            return
        try:
            await self.quest_process_message(message)
        except Exception as e:
            self.logger.error("Failed to process message %s: %s", message.content, e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        if before.flags.loading and not after.flags.loading:
            self.logger.info(
                "Message %s finished loading - relogging final state from %s in %s",
                after.id,
                after.author,
                after.channel,
            )
            self._log_message(after)
            return
        changes: list[str] = []

        if before.content != after.content:
            changes.append(f"content: '{before.content}' -> '{after.content}'")

        if before.embeds != after.embeds:
            changes.append(f"embeds: {len(before.embeds)} -> {len(after.embeds)}")

        if before.flags.value != after.flags.value:
            before_flags = [name for name, value in before.flags if value]
            after_flags = [name for name, value in after.flags if value]
            changes.append(f"flags: {before_flags} -> {after_flags}")

        if before.attachments != after.attachments:
            changes.append(
                f"attachments: {len(before.attachments)} -> {len(after.attachments)}"
            )

        if before.pinned != after.pinned:
            changes.append(f"pinned: {before.pinned} -> {after.pinned}")

        if changes:
            self.logger.info(
                "Message edited by %s in %s | Changes: %s",
                after.author,
                after.channel,
                ", ".join(changes),
            )

    async def quest_process_message(self, message: Message):
        if len(message.content) >= 5:
            res = self.process_fuzzy_message(message, MORNING_QUEST, MORNING_ANSWERS)
            if res:
                return await message.channel.send(res)

            res = self.process_fuzzy_message(message, EVENING_QUEST, EVENING_ANSWERS)
            if res:
                return await message.channel.send(res)

    def process_fuzzy_message(
        self,
        message: Message,
        quests: list[str],
        answers: list[str],
        threshold: int = DEFAULT_FUZZY_THRESHOLD,
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
            message.content, quests, limit=FUZZY_MATCH_LIMIT
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
            return secrets.choice(answers or [None])

        return None

    def _log_message(self, message: Message):
        msg = '%s sended - "%s" in %s (%s)'
        flags = {
            "has_attachments": bool(message.attachments),
            "has_embeds": bool(message.embeds),
            "has_stickers": bool(message.stickers),
            "has_components": bool(message.components),
            "has_reference": bool(message.reference),
            "has_poll": bool(message.poll),
        }
        self.logger.info(
            msg,
            message.author,
            message.content,
            message.channel,
            ", ".join(k for k in flags if flags[k]),
        )

        if message.attachments:
            attachments = tuple(
                ((x.url, x.content_type, x.filename) for x in message.attachments)
            )
            self.logger.info("Attachments: %s", attachments)
        if message.embeds:
            embed_info: list[dict[str, Any]] = []
            for embed in message.embeds:
                embed_dict: dict[str, Any] = {
                    "type": embed.type,
                    "title": embed.title,
                    "description": embed.description[:100]
                    if embed.description
                    else None,
                    "url": embed.url,
                    "color": embed.color,
                    "footer": embed.footer.text if embed.footer else None,
                    "author": embed.author.name if embed.author else None,
                    "field_count": len(embed.fields) if embed.fields else 0,
                    "has_image": embed.image,
                    "has_thumbnail": embed.thumbnail,
                    "has_video": embed.video,
                }
                embed_info.append(embed_dict)
            self.logger.debug("Embeds: %s", embed_info)

        if message.stickers:
            sticker_info = tuple(
                (s.id, s.name, s.format.name) for s in message.stickers
            )
            self.logger.info("Stickers: %s", sticker_info)

        if message.reference:
            ref_info: dict[str, Any] = {
                "message_id": message.reference.message_id,
                "channel_id": message.reference.channel_id,
                "guild_id": message.reference.guild_id,
                "type": message.type.name,
            }
            self.logger.info("Message reference: %s", ref_info)

        if message.components:
            component_types = [type(comp).__name__ for comp in message.components]
            self.logger.debug("Components: %s", component_types)

        if message.flags.value:
            flag_names = [name for name, value in message.flags if value]
            self.logger.debug("Flags: %s", flag_names)

        if message.poll:
            poll_info: dict[str, Any] = {
                "question": message.poll.question,
                "options": message.poll.answers,
                "duration": message.poll.duration.total_seconds(),
            }
            self.logger.info("Poll: %s", poll_info)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(OnMessageCog(bot))
