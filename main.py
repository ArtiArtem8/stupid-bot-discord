import asyncio
import logging.config
import os
import time

import discord
from discord import Intents
from discord.ext import commands, tasks

from config import BOT_PREFIX, LOGGING_CONFIG, TOKEN
from utils import format_time_russian

logging.config.dictConfig(LOGGING_CONFIG)

logger = logging.getLogger("StupidBot")

intents = Intents.default()
intents.messages = True
intents.presences = True
intents.message_content = True
intents.guilds = True


class StupidBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=BOT_PREFIX, intents=intents)
        self.start_time = time.time()

    async def setup_hook(self) -> None:
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"cogs.{filename.removesuffix('.py')}")
                    logger.info("Loaded cog: %s", filename)
                except Exception as e:
                    logger.error(
                        "Failed to load cog %s: %s", filename, e, exc_info=True
                    )
        await self.tree.sync()  # for slash apps
        logger.info("Application commands synced")


bot = StupidBot()


@bot.event
async def on_ready():
    logger.info("Program started ----------------------")
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    timer.start()


@tasks.loop(seconds=11)
async def timer():
    uptime = time.time() - bot.start_time
    formatted_time = format_time_russian(uptime, depth=1)
    activity = discord.Game(
        f"жизнь уже {formatted_time}."
    )  # Играет в жизнь уже %s сек.
    await bot.change_presence(activity=activity)


# Run the bot
if __name__ == "__main__":

    async def main():
        async with bot:
            await bot.start(TOKEN)

    logger.info("Starting bot...")
    asyncio.run(main())
