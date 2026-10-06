import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import override

import discord
from discord import Intents
from discord.ext import commands, tasks

import config
from api.birthday import BirthdayManager
from api.blocking import BlockManager
from api.guild_monitoring import ServerMonitoringManager
from api.reporting import handle_report_button
from framework.cog_loader import CogLoader
from framework.error_handler import handle_app_command_error
from framework.feedback_ui import FeedbackUI
from framework.uptime_manager import UptimeManager
from repositories.birthday_repository import BirthdayRepository
from repositories.blocking_repository import BlockingRepository
from repositories.monitor_repository import MonitorRepository
from repositories.question_repository import QuestionRepository
from repositories.report_repository import ReportRepository
from repositories.sqlite.database import Database, open_engine, validate_schema
from repositories.uptime_repository import UptimeRepository
from repositories.voice_journal import VoiceJournal
from repositories.voice_repository import VoiceRepository
from repositories.volume_repository import VolumeRepository
from utils.russian_time_utils import format_duration_ru

logger = logging.getLogger(__name__)


class DevServer:
    id = 748606123065475134


class StupidBot(commands.Bot):
    """Own cogs, background tasks, uptime and the shared database.

    Startup requires a prepared database. Cogs stop before the final uptime save,
    then database shutdown drains admitted transactions. Migrations and import
    belong to maintenance, never startup. Do not use managers after close.
    """

    def __init__(
        self,
        watch_cogs: bool = False,
        uptime_manager: UptimeManager | None = None,
        cog_loader: CogLoader | None = None,
        database_path: Path | None = None,
    ) -> None:
        intents = Intents.default()
        intents.presences = True
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=config.BOT_PREFIX,
            intents=intents,
            help_command=None,
            enable_debug_events=config.VOICE_PROBE_ENABLED,
        )
        self.tree.error(handle_app_command_error)
        self.owner_id = (
            int(config.DISCORD_BOT_OWNER_ID) if config.DISCORD_BOT_OWNER_ID else None
        )

        self.cog_loader = cog_loader or CogLoader(self, watch=watch_cogs)
        self._database = Database(
            open_engine(database_path or config.DATA_DIR / "app.sqlite")
        )
        self.uptime_manager = uptime_manager or UptimeManager(
            UptimeRepository(self._database)
        )
        self.birthday_manager = BirthdayManager(BirthdayRepository(self._database))
        self.volume_repository = VolumeRepository(self._database)
        self.block_manager = BlockManager(BlockingRepository(self._database))
        self.question_repository = QuestionRepository(self._database)
        self.report_repository = ReportRepository(self._database)
        self.monitor_manager = ServerMonitoringManager(
            MonitorRepository(self._database)
        )
        self._voice_repository = VoiceRepository(self._database)
        self._voice_journal: VoiceJournal | None = None
        self._prepared = False
        self._prepare_lock = asyncio.Lock()
        self._startup_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None

    def create_voice_journal(self) -> VoiceJournal:
        """Give a collector reload a fresh queue only after its predecessor drained."""
        if self._shutdown_task is not None:
            raise RuntimeError("Application is closing")
        if self._voice_journal is not None and not self._voice_journal.closed:
            raise RuntimeError("Previous voice collector has not drained")
        self._voice_journal = VoiceJournal(
            self._voice_repository,
            queue_size=config.VOICE_PROBE_EVENT_QUEUE_MAX,
            batch_size=config.VOICE_PROBE_WRITER_BATCH_MAX,
        )
        return self._voice_journal

    async def restore_state(self) -> None:
        """Validate persisted state before restoring uptime or starting producers."""
        async with self._prepare_lock:
            if self._shutdown_task is not None:
                raise RuntimeError("Application is closing")
            if self._prepared:
                return
            logger.info(
                "Validating SQLite database: %s", self._database.engine.url.database
            )
            await validate_schema(self._database)
            logger.info("SQLite schema validated; storage is COMPLETE")
            await self.birthday_manager.repo.recover_deliveries()
            await self.uptime_manager.restore_uptime()
            self._prepared = True

    async def save_state(self) -> float:
        """Persist accumulated uptime and return the saved duration in seconds."""
        return await self.uptime_manager.save_state()

    @override
    async def close(self) -> None:
        """Stop startup and join background work before unloading cogs and Discord.

        Concurrent callers share one shutdown task. Caller cancellation waits
        for shutdown before propagating, keeping the final save after autosave.
        Independent cancellation of the shutdown task propagates immediately
        once that task finishes; it is never retried.
        """
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._close_owned_work())
        cancellation: asyncio.CancelledError | None = None
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError as error:
                cancellation = error
        self._shutdown_task.result()
        if cancellation is not None:
            raise cancellation

    async def _close_owned_work(self) -> None:
        try:
            if self._startup_task is not None:
                self._startup_task.cancel()
            try:
                await self.cog_loader.close()
            except Exception:
                logger.exception("Failed to stop cog watcher")
            if self._startup_task is not None:
                # setup_hook propagates startup errors; shutdown only joins it.
                await asyncio.gather(self._startup_task, return_exceptions=True)
            await self._stop_background_loops()
        finally:
            try:
                await super().close()
            finally:
                await self._close_storage()

    async def _close_storage(self) -> None:
        """Drain voice, save final uptime and close the database despite failures."""
        try:
            try:
                if self._voice_journal is not None:
                    await self._voice_journal.close()
            finally:
                async with self._prepare_lock:
                    if self._prepared:
                        uptime = await self.uptime_manager.save_state(final=True)
                        logger.info("Final saved uptime: %.0f seconds", uptime)
        finally:
            await self._database.close()

    async def _stop_background_loops(self) -> None:
        pending: list[tuple[str, asyncio.Task[None]]] = []
        for loop in (self.update_activity_task, self.autosave_task):
            task = loop.get_task()
            loop.cancel()
            if task is not None:
                pending.append((loop.coro.__name__, task))
        for name, task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Bot background task %s failed", name)

    @override
    async def setup_hook(self) -> None:
        """Run startup once; closing stops further loads, sync and loop startup.

        A separate owned task lets close cancel and join startup without waiting
        on the caller of setup_hook, which may itself be awaiting close.
        """
        if self._shutdown_task is not None:
            return
        if self._startup_task is None:
            self._startup_task = asyncio.create_task(self._setup())
        await self._startup_task

    async def _setup(self) -> None:
        if self._shutdown_task is not None:
            return
        FeedbackUI.configure(partial(handle_report_button, self.report_repository))
        await self.restore_state()
        await self.cog_loader.load_cogs()
        if self._shutdown_task is not None:
            return
        commands = await self.tree.sync()
        if self._shutdown_task is not None:
            return
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
        uptime = self.uptime_manager.elapsed_microseconds() / 1_000_000
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
