"""Run explicit pilot maintenance without importing the bot's lifecycle owners."""

import argparse
import asyncio
import sys
from pathlib import Path

from experiments.birthday_sqlite.database import copy_database, migrate, open_engine
from experiments.birthday_sqlite.import_json import load_birthdays
from experiments.birthday_sqlite.repository import SQLiteBirthdayRepository
from utils.asyncio_utils import run_in_thread


class Arguments(argparse.Namespace):
    database: Path = Path()
    command: str = ""
    source: Path = Path()
    destination: Path = Path()


async def _run(args: Arguments) -> None:
    match args.command:
        case "migrate":
            await run_in_thread(lambda: migrate(args.database))
        case "backup":
            await run_in_thread(lambda: copy_database(args.database, args.destination))
        case "restore":
            await run_in_thread(lambda: copy_database(args.source, args.database))
        case "import-json":
            configs = await run_in_thread(lambda: load_birthdays(args.source))
            engine = open_engine(args.database)
            try:
                inserted = await SQLiteBirthdayRepository(engine).import_guilds(configs)
                identical = len(configs) - inserted
                sys.stdout.write(
                    f"Imported {inserted} guilds; {identical} already identical.\n"
                )
            finally:
                await engine.dispose()
        case _:
            raise ValueError("Unknown pilot operation")


def main() -> None:
    """Require explicit paths for all operations; never select production by default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("import-json").add_argument("source", type=Path)
    commands.add_parser("backup").add_argument("destination", type=Path)
    commands.add_parser("restore").add_argument("source", type=Path)
    args = parser.parse_args(namespace=Arguments())
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
