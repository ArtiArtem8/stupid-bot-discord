import argparse
import asyncio
import logging.config
import os
import time
from datetime import timedelta
from pathlib import Path

import discord
from discord import Intents, Interaction, app_commands
from discord.ext import commands, tasks
from discord.utils import utcnow

from config import (
    AUTOSAVE_LAST_RUN_FILE_INTERVAL,
    BOT_PREFIX,
    DISCONNECT_TIMER_THRESHOLD,
    DISCORD_BOT_OWNER_ID,
    DISCORD_BOT_TOKEN,
    LAST_RUN_FILE,
    LOGGING_CONFIG,
)
from utils import (
    BlockedUserError,
    FailureUI,
    NoGuildError,
    format_time_russian,
    get_json,
    save_json,
)

logging.config.dictConfig(LOGGING_CONFIG)

logger = logging.getLogger("StupidBot")

intents = Intents.default()
intents.messages = True
intents.presences = True
intents.message_content = True
intents.members = True
intents.guilds = True


class CustomErrorCommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        """Event handler for when an app command error occurs."""
        if isinstance(error, BlockedUserError):
            await interaction.response.send_message(
                "⛔ Доступ к командам запрещён.", ephemeral=True
            )
            return
        elif isinstance(error, NoGuildError):
            await interaction.response.send_message(
                "Команда может быть использована только на сервере",
                ephemeral=True,
                silent=True,
            )
            return
        elif isinstance(error, app_commands.CommandOnCooldown):
            expire_at = utcnow() + timedelta(seconds=error.retry_after)
            await interaction.response.send_message(
                f"Время ожидания: {discord.utils.format_dt(expire_at, 'R')}",
                ephemeral=True,
                silent=True,
            )
            return
        await FailureUI.send_failure(
            interaction, title=str(error), delete_after=300.0, ephemeral=False
        )
        logger.error(f"Unhandled app command error: {error}", exc_info=error)


class StupidBot(commands.Bot):
    def __init__(self):
        """Initialize the StupidBot instance.

        Sets up the command prefix, intents, and initializes various
        attributes related to the bot's uptime and activity monitoring.
        Calls the method to load previous uptime data.
        """
        super().__init__(
            command_prefix=BOT_PREFIX, intents=intents, tree_cls=CustomErrorCommandTree
        )
        if DISCORD_BOT_OWNER_ID:
            self.owner_id = int(DISCORD_BOT_OWNER_ID)
        self.start_time = time.time()
        self.last_activity_str = "N/A"
        self.enable_watch = False
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
        cogs_dir = Path.cwd() / "cogs"
        for file_path in cogs_dir.rglob("*_cog.py"):
            if file_path.name.startswith("_"):
                continue
            rel_path = file_path.relative_to(cogs_dir).with_suffix("")
            dotted_path = ".".join(rel_path.parts)
            try:
                logger.debug("Loading cog: %s", file_path.relative_to(cogs_dir))
                await self.load_extension(f"cogs.{dotted_path}")
                logger.info("Loaded cog: %s", file_path.relative_to(cogs_dir))
            except Exception as e:
                logger.error("Failed to load cog %s: %s", file_path, e, exc_info=True)
        await self.tree.sync()
        logger.info("Application commands synced")
        if self.enable_watch:
            self._watcher = self.loop.create_task(self._cog_watcher())
            logger.info("Cog watcher enabled (argument provided).")

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


def load_last_run() -> dict[str, int] | None:
    """Load the last run info if available."""
    data = get_json(LAST_RUN_FILE)

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
            backup_amount=1,
        )
    except Exception as e:
        logger.error("Failed to save last run data: %s", e)


bot = StupidBot()


@bot.event
async def on_ready():
    """Event handler for when the bot is ready.

    Logs the bot's username and ID, and starts the timer and autosave tasks.
    """
    discord_version = discord.__version__
    logger.info("Program started ----------------------")
    logger.info(
        "Logged in as %s (ID: %s) (API Version: %s)",
        bot.user,
        bot.user.id if bot.user else "Can't get id",
        discord_version,
    )
    logger.debug(
        "Bot owner: %s (%s)",
        bot.owner_id,
        bot.owner_ids if bot.owner_ids else "Not a group",
    )
    timer.start()
    autosave.start()


@tasks.loop(seconds=11)
async def timer():
    """Task to periodically update the bot's activity with its uptime.

    This task updates the bot's activity to reflect its current uptime.
    The uptime is formatted using :func:`format_time_russian` and the
    bot's activity is updated every 11 seconds.
    """
    uptime = time.time() - bot.start_time
    formatted_time = format_time_russian(int(uptime), depth=1)
    activity_str = f"жизнь уже {formatted_time}."
    if activity_str == bot.last_activity_str:
        return
    bot.last_activity_str = activity_str

    game_act = discord.Game(name=activity_str)
    game_act.platform = "IRL"
    await bot.change_presence(activity=game_act)


@tasks.loop(seconds=AUTOSAVE_LAST_RUN_FILE_INTERVAL)
async def autosave():
    """Periodically save the current uptime in case of an emergency shutdown."""
    uptime = time.time() - bot.start_time
    save_last_run(uptime)
    logger.info("Autosaved uptime: %.0f seconds", uptime)


# Run the bot
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Discord bot.")
    parser.add_argument(
        "-w",
        "--watch-cogs",
        action="store_true",
        help="Enables watcher that will reload cogs on code changes.",
    )
    args = parser.parse_args()

    bot.enable_watch = args.watch_cogs

    async def main():
        """Main entry point for the bot."""
        async with bot:
            await bot.start(DISCORD_BOT_TOKEN or "Token_is_missing")

    logger.info("Starting bot...")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        uptime = time.time() - bot.start_time
        save_last_run(uptime)
        logger.info("Program stopped. Uptime saved: %.0f seconds", uptime)
