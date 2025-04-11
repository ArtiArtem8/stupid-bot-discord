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
        self._watcher = self.loop.create_task(self._cog_watcher())

    async def _cog_watcher(self):
        print("Watching for changes...")
        last = time.time()
        while True:
            extensions: set[str] = set()
            for name, module in self.extensions.items():
                if module.__file__ and os.stat(module.__file__).st_mtime > last:
                    extensions.add(name)
            for ext in extensions:
                try:
                    await self.reload_extension(ext)
                    print(f"Reloaded {ext}")
                except commands.ExtensionError as e:
                    print(f"Failed to reload {ext}: {e}")
            last = time.time()
            await asyncio.sleep(1)

    async def _load_extensions(self):
        print("Loading extensions...")
        for file in self.ext_dir.rglob("*.py"):
            if file.stem.startswith("_"):
                continue
            try:
                await self.load_extension(".".join(file.with_suffix("").parts))
                print(f"Loaded {file}")
            except commands.ExtensionError as e:
                print(f"Failed to load {file}: {e}")


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
