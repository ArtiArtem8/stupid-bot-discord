"""Own shared SQLite connections, migrations and transaction shutdown."""

import asyncio
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, String, create_engine, event, select, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry

from stupidbot.common.asyncio import wait_for_completion
from stupidbot.storage.schema import storage_state

SCHEMA_REVISION = "0005_korean"


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Process-local admission and contention counts, unrelated to data revisions."""

    admitted: int
    closing: bool
    busy: int
    checkout_timeouts: int


def is_sqlite_contention(error: OperationalError) -> bool:
    """Recognize SQLite busy/locked errors, including their extended result codes."""
    code = getattr(error.orig, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in (
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    )


class Database:
    """Own one engine and drain admitted transactions before disposing it.

    Application repositories share this owner. Admission counts include pool waiters;
    closing rejects later operations and waits for transaction exit, including
    rollback, before disposal. SQLAlchemy alone schedules connection checkouts.
    Raw engine access is for maintenance and diagnostics, outside runtime work.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._closing: asyncio.Task[None] | None = None
        self._busy = 0
        self._checkout_timeouts = 0

    @property
    def health(self) -> DatabaseHealth:
        """Read current admissions and lifetime failure counts without a checkout."""
        return DatabaseHealth(
            self._active,
            self._closing is not None,
            self._busy,
            self._checkout_timeouts,
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncConnection]:
        """Admit one operation until its connection and transaction are released."""
        if self._closing is not None:
            raise RuntimeError("Database is closing")
        # No await separates admission from increment; all access uses one loop.
        self._active += 1
        self._drained.clear()
        try:
            async with self.engine.begin() as connection:
                yield connection
        except PoolTimeoutError:
            self._checkout_timeouts += 1
            raise
        except OperationalError as exc:
            if is_sqlite_contention(exc):
                self._busy += 1
            raise
        finally:
            self._active -= 1
            if self._active == 0:
                self._drained.set()

    async def close(self) -> None:
        """Reject new work and finish draining even when a close caller cancels."""
        if self._closing is None:
            self._closing = asyncio.create_task(self._dispose_when_drained())
        await wait_for_completion(self._closing)

    async def _dispose_when_drained(self) -> None:
        await self._drained.wait()
        await self.engine.dispose()


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
    """Open an existing database with a single-owner, bounded connection pool.

    The caller owns disposal and must migrate the file before use. One checkout
    spans a complete transaction; concurrent operations wait in SQLAlchemy's
    queue, including reads.
    Do not share a checked-out connection with independently scheduled tasks.
    """
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=path.absolute().as_uri(),
            query={"mode": "rw", "uri": "true"},
        ),
        connect_args={"isolation_level": None, "timeout": 5.0},
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=10,
    )
    event.listen(engine.sync_engine, "connect", _configure_connection)
    event.listen(engine.sync_engine, "begin", _begin)
    return engine


async def validate_schema(database: Database) -> None:
    """Reject unprepared or incompatible databases without running migrations.

    Connection and schema errors propagate to startup. No empty database or JSON
    fallback is allowed. Revision admission is separate from backup integrity.
    """
    path = database.engine.url.database
    if path is not None and path.endswith(".building.sqlite"):
        raise RuntimeError("SQLite import has not been published")
    async with database.transaction() as connection:
        versions = await connection.scalars(
            text("SELECT version_num FROM alembic_version").columns(version_num=String)
        )
        if list(versions) != [SCHEMA_REVISION]:
            raise RuntimeError("Unsupported SQLite schema; run explicit maintenance")
        state = await connection.scalar(
            select(storage_state.c.status).where(storage_state.c.singleton == 1)
        )
        if state != "COMPLETE":
            raise RuntimeError("SQLite import is not complete")


def migrate(path: Path, revision: str = "head") -> None:
    """Upgrade the shared database through Alembic; call outside the event loop.

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
            command.upgrade(settings, revision)
    finally:
        engine.dispose()


def copy_database(source: Path, destination: Path) -> None:
    """Backup or restore with SQLite's online API to a new file only.

    The source is read-only and may be live. Existing destinations are never
    overwritten. Restore by opening the new file, never by replacing an active
    database. Call outside the event loop; errors propagate to the operator.
    """
    with closing(
        sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
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
