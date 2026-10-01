import asyncio
import logging
import time
import typing
from pathlib import Path

from discord.ext import commands

import config

logger = logging.getLogger(__name__)


class CogLoader:
    """Load discovered cogs and own the optional entry-file watcher lifetime."""

    def __init__(self, bot: commands.Bot, watch: bool = False) -> None:
        self.bot = bot
        self.enable_watch = watch
        self._watcher_task: asyncio.Task[typing.NoReturn] | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def load_cogs(self) -> None:
        for file_path in config.COGS_DIR.rglob("*_cog.py"):
            if file_path.name.startswith("_"):
                continue
            rel_path = file_path.relative_to(config.BASE_DIR)
            module_name = ".".join(rel_path.parts).removesuffix(".py")
            logger.debug("Relative path: %s", rel_path)
            logger.debug("Loading: %s", module_name)
            await self.bot.load_extension(module_name)
            logger.info("Loaded: %s", module_name)

    def start_watcher(self) -> None:
        """Start at most one watcher, provided the loader has not been closed."""
        if (
            self.enable_watch
            and self._close_task is None
            and (self._watcher_task is None or self._watcher_task.done())
        ):
            self._watcher_task = asyncio.create_task(self._cog_watcher())
            logger.info("Cog watcher enabled (argument provided).")

    async def close(self) -> None:
        """Join the watcher once, shielding cleanup from caller cancellation."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop_watcher())
        await asyncio.shield(self._close_task)

    async def _stop_watcher(self) -> None:
        if self._watcher_task is None:
            return
        self._watcher_task.cancel()
        try:
            await self._watcher_task
        except asyncio.CancelledError:
            pass

    async def _cog_watcher(self) -> typing.NoReturn:
        """Watch loaded extension entry files; imported modules/assets need restart."""
        logger.info("Watching for changes...")
        last_check = time.time()
        while True:
            extensions: set[str] = set()
            for name, module in self.bot.extensions.items():
                try:
                    if (
                        module.__file__
                        and Path(module.__file__).stat().st_mtime > last_check
                    ):
                        extensions.add(name)
                except OSError:
                    pass
            for ext in extensions:
                try:
                    await self.bot.reload_extension(ext)
                    logger.info("Reloaded %s", ext)
                except Exception:
                    logger.exception("Failed to reload %s", ext)
            last_check = time.time()
            await asyncio.sleep(1)
