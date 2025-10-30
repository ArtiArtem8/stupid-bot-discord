"""Prefix blocker that suggests slash commands.

Detects old-style prefix commands and suggests modern slash command alternatives.
"""

import logging
import time

from discord import Message
from discord.ext import commands
from fuzzywuzzy.process import extractOne  # type: ignore

SUGGESTION_THRESHOLD = 25
"""Minimum fuzzy match score to suggest command"""


class PrefixBlockerCog(commands.Cog):
    """Redirect users from prefix commands to slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("PrefixBlockerCog")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        prefixes = await self.bot.get_prefix(message)
        prefixes = [prefixes] if isinstance(prefixes, str) else prefixes

        # Find which prefix was used (if any)
        if not any((message.content.startswith(i), pref := i)[0] for i in prefixes):
            return

        raw_content = message.content.removeprefix(pref).strip()

        if not raw_content:
            return

        slash_commands = [cmd.name for cmd in self.bot.tree.get_commands()]
        suggestion = None
        if slash_commands:
            best_match: tuple[str, int] = extractOne(raw_content, slash_commands)  # type: ignore
            if best_match and best_match[1] >= SUGGESTION_THRESHOLD:
                suggestion = best_match[0]

        response = "Префиксы убраны; воспользуйтесь слэш-командами."
        if suggestion:
            response += f" (возможно вы имели в виду `/{suggestion}`)"
        try:
            delete_after = 30
            timer = f"Удалится <t:{int(time.time() + delete_after)}:R>"
            await message.reply(
                response + "\n" + timer, mention_author=False, delete_after=delete_after
            )
        except Exception as e:
            self.logger.error("Failed to send prefix warning: %s", e)


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(PrefixBlockerCog(bot))
