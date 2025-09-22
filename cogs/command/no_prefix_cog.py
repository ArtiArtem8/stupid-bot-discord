import logging

from discord import Message
from discord.ext import commands
from fuzzywuzzy.process import extractOne  # type: ignore


class PrefixBlockerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("PrefixBlockerCog")

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        prefixes = await self.bot.get_prefix(message)
        prefixes = [prefixes] if isinstance(prefixes, str) else prefixes

        if not any((message.content.startswith(i), pref := i)[0] for i in prefixes):
            return
        raw_content = message.content.removeprefix(pref).strip()

        slash_commands = [cmd.name for cmd in self.bot.tree.get_commands()]
        suggestion = None
        if slash_commands:
            best_match: tuple[str, int] = extractOne(raw_content, slash_commands)  # type: ignore
            threshold = 25
            if best_match and best_match[1] >= threshold:
                suggestion = best_match[0]

        response = "Префиксы убраны; воспользуйтесь слэш-командами."
        if suggestion:
            response += f" (возможно вы имели в виду `/{suggestion}`)"
        try:
            await message.reply(response, mention_author=False, delete_after=30)
        except Exception as e:
            self.logger.error("Failed to send prefix warning: %s", e)


async def setup(bot: commands.Bot):
    """Setup.

    :param commands.Bot bot: BOT ITSELF
    """
    await bot.add_cog(PrefixBlockerCog(bot))
