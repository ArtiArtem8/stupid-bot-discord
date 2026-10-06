"""Create real, migrated SQLite fixtures without accessing application data paths."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from repositories.sqlite.database import Database, migrate, open_engine
from utils.asyncio_utils import run_in_thread


async def temporary_database(test: IsolatedAsyncioTestCase) -> tuple[Path, Database]:
    """Register disposal before temporary-directory cleanup, including failed tests."""
    directory = TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    path = Path(directory.name) / "application.sqlite"
    await run_in_thread(lambda: migrate(path))
    database = Database(open_engine(path))
    test.addAsyncCleanup(database.close)
    return path, database
