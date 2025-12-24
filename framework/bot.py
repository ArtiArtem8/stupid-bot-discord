import logging
import time

import discord
from discord import Intents
from discord.ext import commands, tasks

import config
from api.reporting import handle_report_button
from framework import FeedbackUI
from framework.cog_loader import CogLoader
from framework.error_handler import CustomErrorCommandTree
from framework.uptime_manager import UptimeManager
from utils import format_time_russian

logger = logging.getLogger("StupidBot")


class DevServer:
    id = 748606123065475134


class StupidBot(commands.Bot):
    def __init__(
        self,
        watch_cogs: bool = False,
        uptime_manager: UptimeManager | None = None,
        cog_loader: CogLoader | None = None,
    ):
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
            help_command=None,
        )
        self.owner_id = (
            int(config.DISCORD_BOT_OWNER_ID) if config.DISCORD_BOT_OWNER_ID else None
        )

        self.uptime_manager = uptime_manager or UptimeManager()
        self.cog_loader = cog_loader or CogLoader(self, watch=watch_cogs)

    def save_state(self) -> float:
        """Saves the current uptime state to file."""
        return self.uptime_manager.save_state()

    async def setup_hook(self) -> None:
        FeedbackUI.configure(handle_report_button)
        await self.cog_loader.load_cogs()
        commands = await self.tree.sync()
        logger.info("Application commands synced")
        logger.debug("Synced commands: %s", commands)
        self.update_activity_task.start()
        self.autosave_task.start()
        self.cog_loader.start_watcher()

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
        uptime = time.time() - self.uptime_manager.start_time
        formatted_time = format_time_russian(int(uptime), depth=1)
        activity_str = f"жизнь уже {formatted_time}."

        if activity_str == self.uptime_manager.last_activity_str:
            return

        self.uptime_manager.last_activity_str = activity_str
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
