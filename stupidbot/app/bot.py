import asyncio
import logging
from functools import partial
from math import isfinite
from pathlib import Path
from typing import override

import discord
from discord import Intents
from discord.ext import commands, tasks
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from stupidbot import settings as config
from stupidbot.app.cog_loader import CogLoader
from stupidbot.app.health import (
    ComponentHealth,
    HealthSnapshot,
    TaskHealth,
    component_health,
)
from stupidbot.app.messages import AppMessages
from stupidbot.app.uptime import UptimeManager
from stupidbot.app.uptime_repository import UptimeRepository
from stupidbot.common.asyncio import wait_for_completion
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.access.operations import BlockManager
from stupidbot.features.access.repository import BlockingRepository
from stupidbot.features.ask.repository import QuestionRepository
from stupidbot.features.birthdays.repository import BirthdayRepository
from stupidbot.features.member_roles.repository import MonitorRepository
from stupidbot.features.member_roles.restoration import ServerMonitoringManager
from stupidbot.features.music.repository import VolumeRepository
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.features.reports.repository import ReportRepository
from stupidbot.features.reports.ui import handle_report_button
from stupidbot.features.voice.journal import VoiceJournal
from stupidbot.features.voice.repository import VoiceRepository
from stupidbot.features.voice.runtime import VoiceAnalytics
from stupidbot.i18n.catalog import catalog, catalogs
from stupidbot.i18n.commands import CommandTranslator
from stupidbot.i18n.policy import Language
from stupidbot.storage.database import (
    Database,
    is_sqlite_contention,
    open_engine,
    validate_schema,
)

logger = logging.getLogger(__name__)


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
        self.birthday_repository = BirthdayRepository(self._database)
        self.volume_repository = VolumeRepository(self._database)
        self.preferences_repository = PreferencesRepository(self._database)
        self.block_manager = BlockManager(BlockingRepository(self._database))
        self.question_repository = QuestionRepository(self._database)
        self.report_repository = ReportRepository(self._database)
        self.monitor_manager = ServerMonitoringManager(
            MonitorRepository(self._database)
        )
        self.voice_analytics = VoiceAnalytics(VoiceRepository(self._database))
        self._voice_journal: VoiceJournal | None = None
        self._prepared = False
        self._prepare_lock = asyncio.Lock()
        self._startup_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._health_failures = (0, 0, 0, 0)

    def health_snapshot(self) -> HealthSnapshot:
        """Read local status even when database admission or external services fail."""
        components = component_health(self.cogs)
        loops = (self.update_activity_task, self.autosave_task, self.health_task)
        tasks = [
            TaskHealth(loop.coro.__name__, loop.is_running(), loop.failed())
            for loop in loops
        ]
        for component in components.values():
            tasks.extend(component.tasks)
        tasks.extend(self.cog_loader.runtime_health.tasks)
        checkpoint = self.uptime_manager.checkpoint_microseconds
        journal = self._voice_journal
        unavailable = ComponentHealth(False)
        return HealthSnapshot(
            ready=self.is_ready(),
            latency_ms=self.latency * 1000 if isfinite(self.latency) else None,
            uptime_seconds=(
                self.uptime_manager.elapsed_microseconds() / 1_000_000
                if checkpoint is not None
                else None
            ),
            checkpoint_us=checkpoint,
            database=self._database.health,
            tasks=tuple(tasks),
            voice_enabled=config.VOICE_PROBE_ENABLED,
            voice_writer=journal is not None and journal.writer_running,
            voice_pending=journal.pending if journal is not None else 0,
            voice_counts=journal.counts if journal is not None else None,
            voice_last_commit_at=journal.last_commit_at
            if journal is not None
            else None,
            analytics=self.voice_analytics.status,
            music_ready=components.get("MusicCog", unavailable).ready,
            renderer_ready=components.get("VoiceProfileCog", unavailable).ready,
        )

    def create_voice_journal(self) -> VoiceJournal:
        """Give a collector reload a fresh queue only after its predecessor drained."""
        if self._shutdown_task is not None:
            raise RuntimeError("Application is closing")
        if self._voice_journal is not None and not self._voice_journal.closed:
            raise RuntimeError("Previous voice collector has not drained")
        self._voice_journal = VoiceJournal(
            self.voice_analytics,
            queue_size=config.VOICE_PROBE_EVENT_QUEUE_MAX,
            batch_size=config.VOICE_PROBE_WRITER_BATCH_MAX,
        )
        # Journal counters restart with their owner; database totals continue.
        db_busy, db_timeouts, _, _ = self._health_failures
        self._health_failures = (db_busy, db_timeouts, 0, 0)
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
            await self.birthday_repository.recover_deliveries()
            await self.uptime_manager.restore_uptime()
            await self.voice_analytics.start()
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
        await wait_for_completion(self._shutdown_task)

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
                try:
                    await self.voice_analytics.close()
                finally:
                    async with self._prepare_lock:
                        if self._prepared:
                            uptime = await self.uptime_manager.save_state(final=True)
                            logger.info("Final saved uptime: %.0f seconds", uptime)
        finally:
            await self._database.close()

    async def _stop_background_loops(self) -> None:
        pending: list[tuple[str, asyncio.Task[None]]] = []
        for loop in (self.update_activity_task, self.autosave_task, self.health_task):
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
        await asyncio.to_thread(catalogs)
        await self.tree.set_translator(CommandTranslator())
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
        self.health_task.start()
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
        # Discord exposes one bot-wide presence, without a guild or user audience.
        activity_str = AppMessages(catalog("app", Language.RU)).presence(
            seconds=int(uptime)
        )

        if activity_str == self.uptime_manager.last_activity_str:
            return

        await self.change_presence(
            activity=discord.Game(name=activity_str, platform="IRL")
        )
        self.uptime_manager.last_activity_str = activity_str

    @tasks.loop(seconds=config.AUTOSAVE_UPTIME_INTERVAL)
    async def autosave_task(self) -> None:
        try:
            uptime = await self.save_state()
        except PoolTimeoutError:
            logger.warning("Uptime checkpoint deferred: database checkout timed out")
            return
        except OperationalError as exc:
            if not is_sqlite_contention(exc):
                raise
            logger.warning("Uptime checkpoint deferred: SQLite busy/locked")
            return
        logger.debug("Autosaved uptime: %.0f seconds", uptime)

    @tasks.loop(minutes=5)
    async def health_task(self) -> None:
        snapshot = self.health_snapshot()
        failures = snapshot.failures
        new_failure = any(
            current > previous
            for current, previous in zip(failures, self._health_failures, strict=True)
        )
        self._health_failures = failures
        level = logging.WARNING if snapshot.degraded or new_failure else logging.INFO
        logger.log(level, "Runtime health: %s", " | ".join(snapshot.lines()))

    @update_activity_task.before_loop
    @autosave_task.before_loop
    @health_task.before_loop
    async def before_tasks(self) -> None:
        await self.wait_until_ready()
