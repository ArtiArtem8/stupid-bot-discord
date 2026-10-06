"""Explicit SQLite initialization, upgrade, backup and restore without Discord."""

import argparse
import asyncio
from pathlib import Path

from repositories.sqlite.database import (
    Database,
    copy_database,
    migrate,
    open_engine,
    validate_schema,
)
from utils.asyncio_utils import run_in_thread


class Arguments(argparse.Namespace):
    database: Path = Path()
    command: str = ""
    source: Path = Path()
    destination: Path = Path()


async def _verify(path: Path) -> None:
    database = Database(open_engine(path))
    try:
        await validate_schema(database)
        async with database.transaction() as connection:
            if (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all():
                raise ValueError("Database has invalid foreign keys")
            if (await connection.exec_driver_sql("PRAGMA quick_check")).all() != [
                ("ok",)
            ]:
                raise ValueError("Database failed structural verification")
    finally:
        await database.close()


async def _run(args: Arguments) -> None:
    match args.command:
        case "migrate":
            await run_in_thread(lambda: migrate(args.database))
            await _verify(args.database)
        case "backup":
            await run_in_thread(lambda: copy_database(args.database, args.destination))
        case "restore":
            await run_in_thread(lambda: copy_database(args.source, args.database))
            await _verify(args.database)
        case "check":
            await _verify(args.database)
        case _:
            raise ValueError("Unknown maintenance operation")


def main() -> None:
    """Require explicit paths; legacy import belongs to tools.migrate_storage_once."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("check")
    commands.add_parser("backup").add_argument("destination", type=Path)
    commands.add_parser("restore").add_argument("source", type=Path)
    args = parser.parse_args(namespace=Arguments())
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
