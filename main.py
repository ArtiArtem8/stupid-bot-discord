import argparse
import asyncio
import logging
import os
import time
from datetime import timedelta

import discord
from discord import Intents, Interaction, app_commands
from discord.ext import commands, tasks
from discord.utils import utcnow

import config
from utils import (
    BlockedUserError,
    FailureUI,
    NoGuildError,
    format_time_russian,
    get_json,
    save_json,
    setup_logging,
)

logger = logging.getLogger("StupidBot")


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
    def __init__(self, watch_cogs: bool = False):
        """Initialize the StupidBot instance.

        Sets up the command prefix, intents, and initializes various
        attributes related to the bot's uptime and activity monitoring.
        Calls the method to load previous uptime data.
        """
        intents = Intents.default()
        intents.presences = True
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=config.BOT_PREFIX,
            intents=intents,
            tree_cls=CustomErrorCommandTree,
        )
        self.owner_id = (
            int(config.DISCORD_BOT_OWNER_ID) if config.DISCORD_BOT_OWNER_ID else None
        )
        self.enable_watch = watch_cogs

        self.start_time: float = time.time()
        self.last_activity_str = "N/A"
        self._restore_uptime()

    def _restore_uptime(self):
        """Logic to resume accumulated uptime if restart was quick."""
        last_run = get_json(config.LAST_RUN_FILE)
        if last_run is None:
            return

        last_shutdown = last_run.get("last_shutdown", 0.0)
        accumulated = last_run.get("accumulated_uptime", 0.0)
        disconnect_time = time.time() - last_shutdown

        if disconnect_time < config.DISCONNECT_TIMER_THRESHOLD:
            self.start_time = time.time() - accumulated
            logger.info("Resuming uptime (Offline for %.0fs)", disconnect_time)
        else:
            logger.info(
                "Offline time (%.0fs) exceeded threshold; Resetting uptime.",
                disconnect_time,
            )

    def save_state(self) -> float:
        """Saves the current uptime state to file."""
        current_uptime = time.time() - self.start_time
        data = {
            "last_shutdown": time.time(),
            "accumulated_uptime": current_uptime,
        }
        try:
            save_json(config.LAST_RUN_FILE, data, backup_amount=1)
        except Exception as e:
            logger.error("Failed to save state: %s", e)
        return current_uptime

    async def setup_hook(self) -> None:
        await self._load_cogs()
        commands = await self.tree.sync()
        logger.info("Application commands synced")
        logger.debug("Synced commands: %s", commands)
        self.update_activity_task.start()
        self.autosave_task.start()
        if self.enable_watch:
            self._watcher = self.loop.create_task(self._cog_watcher())
            logger.info("Cog watcher enabled (argument provided).")

    async def _load_cogs(self):
        for file_path in config.COGS_DIR.rglob("*_cog.py"):
            if file_path.name.startswith("_"):
                continue
            rel_path = file_path.relative_to(config.BASE_DIR)
            module_name = ".".join(rel_path.parts).removesuffix(".py")
            logger.debug("Relative path: %s", rel_path)
            try:
                logger.debug("Loading: %s", module_name)
                await self.load_extension(module_name)
                logger.info("Loaded: %s", module_name)
            except Exception:
                logger.exception("Failed to load %s", module_name)

    async def _cog_watcher(self):
        """Watch for file changes and reload cogs hot."""
        logger.info("Watching for changes...")
        last_check = time.time()
        while True:
            extensions: set[str] = set()
            for name, module in self.extensions.items():
                try:
                    if (
                        module.__file__
                        and os.stat(module.__file__).st_mtime > last_check
                    ):
                        extensions.add(name)
                except OSError:
                    pass
            for ext in extensions:
                try:
                    await self.reload_extension(ext)
                    logger.info("Reloaded %s", ext)
                except Exception:
                    logger.exception(f"Failed to reload {ext}")
            last_check = time.time()
            await asyncio.sleep(1)

    async def on_ready(self) -> None:
        """Event handler for when the bot is ready.

        Logs the bot's username and ID, and starts the timer and autosave tasks.
        """
        logger.info("Bot is ready -------------------------")
        logger.info(
            "Logged in as %s (ID: %s) (API Version: %s)",
            self.user,
            self.user.id if self.user else "DISCONNECTED",
            discord.__version__,
        )
        logger.debug(
            "bot's owner: %s (%s)",
            self.owner_id,
            self.owner_ids if self.owner_ids else "Not a group",
        )

    @tasks.loop(seconds=11)
    async def update_activity_task(self) -> None:
        """Task to periodically update the bot's activity with its uptime.

        The uptime is formatted using :func:`format_time_russian` and the
        """
        uptime = time.time() - self.start_time
        formatted_time = format_time_russian(int(uptime), depth=1)
        activity_str = f"жизнь уже {formatted_time}."

        if activity_str == self.last_activity_str:
            return

        self.last_activity_str = activity_str
        await self.change_presence(
            activity=discord.Game(name=activity_str, platform="IRL")
        )

    @tasks.loop(seconds=config.AUTOSAVE_UPTIME_INTERVAL)
    async def autosave_task(self):
        uptime = self.save_state()
        logger.debug("Autosaved uptime: %.0f seconds", uptime)

    @update_activity_task.before_loop
    @autosave_task.before_loop
    async def before_tasks(self):
        await self.wait_until_ready()


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run the Discord bot.")
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="Enables watcher that will reload cogs on code changes.",
    )
    args = parser.parse_args()

    for dir in [
        config.DATA_DIR,
        config.BACKUP_DIR,
        config.TEMP_DIR,
        config.COGS_DIR,
    ]:
        dir.mkdir(parents=True, exist_ok=True)

    setup_logging(config.ENCODING)

    if not config.DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing in environment/config!")
        return

    bot = StupidBot(watch_cogs=args.watch)

    logger.info("Starting bot...")
    try:
        async with bot:
            await bot.start(config.DISCORD_BOT_TOKEN)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Keyboard Interrupt detected.")
    finally:
        uptime = bot.save_state()
        logger.info(f"Bot stopped. Final saved uptime: {uptime:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
