import logging
import time
from typing import override

import discord
from discord import Intents
from discord.ext import commands, tasks

import config
from api.reporting import handle_report_button
from framework.cog_loader import CogLoader
from framework.error_handler import handle_app_command_error
from framework.feedback_ui import FeedbackUI
from framework.uptime_manager import UptimeManager
from utils.russian_time_utils import format_duration_ru

logger = logging.getLogger("StupidBot")


class DevServer:
    id = 748606123065475134


class StupidBot(commands.Bot):
    """Discord runtime owner for cogs, background tasks, and uptime state."""

    def __init__(
        self,
        watch_cogs: bool = False,
        uptime_manager: UptimeManager | None = None,
        cog_loader: CogLoader | None = None,
    ) -> None:
        intents = Intents.default()
        intents.presences = True
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=config.BOT_PREFIX,
            intents=intents,
            help_command=None,
        )
        self.tree.error(handle_app_command_error)
        self.owner_id = (
            int(config.DISCORD_BOT_OWNER_ID) if config.DISCORD_BOT_OWNER_ID else None
        )

        self.uptime_manager = uptime_manager or UptimeManager()
        self.cog_loader = cog_loader or CogLoader(self, watch=watch_cogs)

    async def restore_state(self) -> None:
        """Restore persisted uptime before the bot starts."""
        await self.uptime_manager.restore_uptime()

    async def save_state(self) -> float:
        """Persist accumulated uptime and return the saved duration in seconds."""
        return await self.uptime_manager.save_state()

    @override
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
            self.owner_ids or "Not a group",
        )

    @tasks.loop(seconds=11)
    async def update_activity_task(self) -> None:
        """Refresh presence only when the formatted uptime changes."""
        uptime = time.time() - self.uptime_manager.start_time
        formatted_time = format_duration_ru(int(uptime), depth=2)
        activity_str = f"жизнь уже {formatted_time}."

        if activity_str == self.uptime_manager.last_activity_str:
            return

        self.uptime_manager.last_activity_str = activity_str
        await self.change_presence(
            activity=discord.Game(name=activity_str, platform="IRL")
        )

    @tasks.loop(seconds=config.AUTOSAVE_UPTIME_INTERVAL)
    async def autosave_task(self) -> None:
        uptime = await self.save_state()
        logger.debug("Autosaved uptime: %.0f seconds", uptime)

    @update_activity_task.before_loop
    @autosave_task.before_loop
    async def before_tasks(self) -> None:
        await self.wait_until_ready()
