import argparse
import asyncio
import logging
import tracemalloc
from pathlib import Path

import config
from framework.bot import StupidBot
from utils.logging_setup import setup_logging

logger = logging.getLogger("StupidBot")


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

    for directory in [
        config.DATA_DIR,
        config.COGS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    setup_logging(config.ENCODING)

    if not config.DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing in environment/config!")
        raise SystemExit(1)

    bot = StupidBot(
        watch_cogs=args.watch,
        database_path=args.database or config.DATA_DIR / "app.sqlite",
    )

    logger.info("Starting bot...")
    try:
        async with bot:
            await bot.restore_state()
            await bot.start(token=config.DISCORD_BOT_TOKEN)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Keyboard Interrupt detected.")
        raise
    finally:
        await bot.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
