"""Log incoming messages before applying greeting filters."""

from typing import TYPE_CHECKING

from discord import Message
from discord.ext import commands

from stupidbot.features.access.operations import BlockManager
from stupidbot.features.greetings.responder import Greetings

from .message_logging import log_message, log_message_edit

if TYPE_CHECKING:
    from .bot import StupidBot


class MessageEventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, blocks: BlockManager) -> None:
        self.greetings = Greetings(bot, blocks)

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        log_message(message)
        await self.greetings.handle_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message) -> None:
        log_message_edit(before, after)


async def setup(bot: "StupidBot") -> None:
    """Register the single message logging and greeting listener."""
    await bot.add_cog(MessageEventsCog(bot, bot.block_manager))
