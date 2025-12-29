import argparse
import asyncio
import logging
import tracemalloc

import config
from framework.bot import StupidBot
from utils import setup_logging

tracemalloc.start()
logger = logging.getLogger("StupidBot")


class Arguments(argparse.Namespace):
    watch: bool = False


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run the Discord bot.")
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="Enables watcher that will reload cogs on code changes.",
    )
    args = parser.parse_args(namespace=Arguments())

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
            await bot.start(token=config.DISCORD_BOT_TOKEN)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Keyboard Interrupt detected.")
        raise
    finally:
        uptime = bot.save_state()
        logger.info(f"Bot stopped. Final saved uptime: {uptime:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
