import asyncio
import logging
import os
import time

from discord.ext import commands

import config

LOGGER = logging.getLogger("StupidBot")


class CogLoader:
    def __init__(self, bot: commands.Bot, watch: bool = False):
        self.bot = bot
        self.enable_watch = watch
        self._watcher_task = None

    async def load_cogs(self):
        for file_path in config.COGS_DIR.rglob("*_cog.py"):
            if file_path.name.startswith("_"):
                continue
            rel_path = file_path.relative_to(config.BASE_DIR)
            module_name = ".".join(rel_path.parts).removesuffix(".py")
            LOGGER.debug("Relative path: %s", rel_path)
            try:
                LOGGER.debug("Loading: %s", module_name)
                await self.bot.load_extension(module_name)
                LOGGER.info("Loaded: %s", module_name)
            except Exception:
                LOGGER.exception("Failed to load %s", module_name)

    def start_watcher(self):
        if self.enable_watch:
            self._watcher_task = self.bot.loop.create_task(self._cog_watcher())
            LOGGER.info("Cog watcher enabled (argument provided).")

    async def _cog_watcher(self):
        """Watch for file changes and reload cogs hot."""
        LOGGER.info("Watching for changes...")
        last_check = time.time()
        while True:
            extensions: set[str] = set()
            for name, module in self.bot.extensions.items():
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
                    await self.bot.reload_extension(ext)
                    LOGGER.info("Reloaded %s", ext)
                except Exception:
                    LOGGER.exception(f"Failed to reload {ext}")
            last_check = time.time()
            await asyncio.sleep(1)
