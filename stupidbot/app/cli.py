import argparse
import asyncio
import logging
import signal
import tracemalloc
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from stupidbot import settings as config
from stupidbot.app.bot import StupidBot
from stupidbot.app.logging import setup_logging

logger = logging.getLogger(__name__)


@contextmanager
def _handle_sigterm() -> Generator[asyncio.Event]:
    """Turn the first SIGTERM into application cancellation through final cleanup."""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("Termination handling requires the application task")
    loop = asyncio.get_running_loop()
    requested = asyncio.Event()

    def terminate(_signal: int, _frame: FrameType | None) -> None:
        if not requested.is_set():
            requested.set()
            loop.call_soon_threadsafe(task.cancel)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield requested
    finally:
        signal.signal(signal.SIGTERM, previous)


class Arguments(argparse.Namespace):
    watch: bool = False
    tracemalloc: bool = False
    database: Path | None = None


async def main() -> None:
    """Run the Discord bot until shutdown."""
    parser = argparse.ArgumentParser(description="Run the Discord bot.")
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="Enables watcher that will reload cogs on code changes.",
    )
    parser.add_argument(
        "--tracemalloc",
        action="store_true",
        help="Enable allocation tracing for memory diagnostics (adds overhead).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        metavar="DATABASE",
        default=config.DATA_DIR / "app.sqlite",
        help="Path to the prepared application SQLite database.",
    )
    args = parser.parse_args(namespace=Arguments())
    if args.tracemalloc:
        tracemalloc.start()

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    setup_logging(config.ENCODING)

    if not config.DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing in environment/config!")
        raise SystemExit(1)

    bot = StupidBot(
        watch_cogs=args.watch,
        database_path=args.database or config.DATA_DIR / "app.sqlite",
    )

    logger.info("Starting bot...")
    with _handle_sigterm() as termination:
        try:
            async with bot:
                await bot.restore_state()
                await bot.start(token=config.DISCORD_BOT_TOKEN)
        except asyncio.CancelledError:
            if not termination.is_set():
                raise
            logger.info("SIGTERM received")
        finally:
            await bot.close()
            logger.info("Bot stopped.")


def run() -> None:
    """Own the event loop for both supported application entry points."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
