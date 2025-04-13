import asyncio
import logging.config
import os
import time

import discord
from discord import Intents
from discord.ext import commands, tasks

from config import (
    AUTOSAVE_LAST_RUN_FILE_INTERVAL,
    BOT_PREFIX,
    DISCONNECT_TIMER_THRESHOLD,
    DISCORD_BOT_TOKEN,
    LAST_RUN_FILE,
    LOGGING_CONFIG,
)
from utils import format_time_russian, get_json, save_json

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
        self._load_previous_uptime()

    def _load_previous_uptime(self):
        last_run = load_last_run()
        if last_run is None:
            return
        last_shutdown = last_run.get("last_shutdown", 0)
        accumulated = last_run.get("accumulated_uptime", 0)
        disconnect_time = time.time() - last_shutdown
        if disconnect_time < DISCONNECT_TIMER_THRESHOLD:
            self.start_time = time.time() - accumulated
            logger.info(
                "Resuming uptime (bot was offline for %.0f seconds)",
                disconnect_time,
            )
        else:
            logger.info(
                "Offline time (%.0f seconds) exceeded threshold; starting fresh.",
                disconnect_time,
            )

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


def load_last_run() -> dict | None:
    """Load the last run info if available."""
    data = get_json(LAST_RUN_FILE)

    if data is not None and not isinstance(data, dict):
        logger.error("Invalid data format in last run file")
        return None

    if data is None and os.path.exists(LAST_RUN_FILE):
        logger.error("Failed to load last run data (file exists but is invalid)")

    return data


def save_last_run(accumulated_uptime: float) -> None:
    """Save the current shutdown time and accumulated uptime."""
    data = {
        "last_shutdown": time.time(),
        "accumulated_uptime": accumulated_uptime,
    }
    try:
        save_json(
            filename=LAST_RUN_FILE,
            data=data,
            backup_amount=0,
        )
    except Exception as e:
        logger.error("Failed to save last run data: %s", e)


bot = StupidBot()


@bot.event
async def on_ready():
    logger.info("Program started ----------------------")
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    timer.start()
    autosave.start()  # Start the autosave task


@tasks.loop(seconds=11)
async def timer():
    uptime = time.time() - bot.start_time
    formatted_time = format_time_russian(uptime, depth=1)
    activity = discord.Game(
        f"жизнь уже {formatted_time}."
    )  # Играет в жизнь уже %s сек.
    await bot.change_presence(activity=activity)


@tasks.loop(seconds=AUTOSAVE_LAST_RUN_FILE_INTERVAL)
async def autosave():
    """Periodically save the current uptime in case of an emergency shutdown."""
    uptime = time.time() - bot.start_time
    save_last_run(uptime)
    logger.info("Autosaved uptime: %.0f seconds", uptime)


# Run the bot
if __name__ == "__main__":

    async def main():
        async with bot:
            await bot.start(DISCORD_BOT_TOKEN)

    logger.info("Starting bot...")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        uptime = time.time() - bot.start_time
        save_last_run(uptime)
        logger.info("Program stopped. Uptime saved: %.0f seconds", uptime)
