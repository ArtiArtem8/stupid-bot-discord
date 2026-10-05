"""Own pilot connections, explicit migrations and consistent SQLite copies."""

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry


def _configure_connection(
    connection: DBAPIConnection, _entry: ConnectionPoolEntry
) -> None:
    # isolation_level=None disables driver-managed BEGIN. These PRAGMAs run
    # outside a transaction; SQLAlchemy's begin event starts every transaction.
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=FULL")
    finally:
        cursor.close()


def _begin(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")


def open_engine(path: Path) -> AsyncEngine:
    """Create the pilot's single-owner, bounded connection pool.

    The caller owns disposal and must migrate the file before use. One checkout
    spans a complete transaction; concurrent operations wait in SQLAlchemy's
    queue. This also serializes readers and is not a voice workload tuning claim.
    Do not share a checked-out connection with independently scheduled tasks.
    """
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path)),
        connect_args={"isolation_level": None, "timeout": 5.0},
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=10,
    )
    event.listen(engine.sync_engine, "connect", _configure_connection)
    event.listen(engine.sync_engine, "begin", _begin)
    return engine


def migrate(path: Path) -> None:
    """Upgrade a pilot database through Alembic; call outside the event loop.

    This explicit maintenance operation requires exclusive application ownership.
    No migration runs during ordinary repository construction or bot startup.
    The parent directory must already exist.
    """
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"isolation_level": None},
    )
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "begin", _begin)
    try:
        settings = Config()
        settings.set_main_option(
            "script_location", str(Path(__file__).parent / "migrations")
        )
        with engine.begin() as connection:
            settings.attributes["connection"] = connection
            command.upgrade(settings, "head")
    finally:
        engine.dispose()


def copy_database(source: Path, destination: Path) -> None:
    """Backup or restore with SQLite's online API to a new file only.

    The source is read-only and may be live. Existing destinations are never
    overwritten. Restore by opening the new file, never by replacing an active
    database. Call outside the event loop; errors propagate to the operator.
    """
    with closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    ) as reader:
        # Exclusive creation also rejects accidentally using the source as target.
        with destination.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)
                if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite backup failed integrity verification")
        except BaseException:
            destination.unlink()
            raise
