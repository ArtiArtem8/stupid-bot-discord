import argparse
import asyncio
import logging

import config
from framework.bot import StupidBot
from utils import setup_logging

LOGGER = logging.getLogger("StupidBot")


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
        LOGGER.critical("DISCORD_BOT_TOKEN is missing in environment/config!")
        return

    bot = StupidBot(watch_cogs=args.watch)

    LOGGER.info("Starting bot...")
    try:
        async with bot:
            await bot.start(config.DISCORD_BOT_TOKEN)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Keyboard Interrupt detected.")
    finally:
        uptime = bot.save_state()
        LOGGER.info(f"Bot stopped. Final saved uptime: {uptime:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
