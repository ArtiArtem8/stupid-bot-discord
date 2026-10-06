"""Run explicit shared SQLite maintenance without starting the bot."""

import argparse
import asyncio
import sys
from pathlib import Path

from repositories.birthday_sqlite.import_json import load_birthdays
from repositories.birthday_sqlite.repository import SQLiteBirthdayRepository
from repositories.sqlite.database import (
    Database,
    copy_database,
    migrate,
    open_engine,
    validate_schema,
)
from repositories.sqlite.import_json import load_volumes
from repositories.sqlite_volume_repository import SQLiteVolumeRepository
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
        case "import-birthdays" | "import-volumes":
            database = Database(open_engine(args.database))
            try:
                await validate_schema(database.engine)
                inserted, total = await _import(args, database)
                identical = total - inserted
                sys.stdout.write(
                    f"Imported {inserted} guilds; {identical} already identical.\n"
                )
            finally:
                await database.close()
        case _:
            raise ValueError("Unknown maintenance operation")


async def _import(args: Arguments, database: Database) -> tuple[int, int]:
    if args.command == "import-birthdays":
        configs = await run_in_thread(lambda: load_birthdays(args.source))
        return await SQLiteBirthdayRepository(database).import_guilds(configs), len(
            configs
        )
    entries = await run_in_thread(lambda: load_volumes(args.source))
    return await SQLiteVolumeRepository(database).import_volumes(entries), len(entries)


def main() -> None:
    """Require explicit paths for all operations; never select production by default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("import-birthdays").add_argument("source", type=Path)
    commands.add_parser("import-volumes").add_argument("source", type=Path)
    commands.add_parser("backup").add_argument("destination", type=Path)
    commands.add_parser("restore").add_argument("source", type=Path)
    args = parser.parse_args(namespace=Arguments())
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
