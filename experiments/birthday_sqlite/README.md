# Birthday SQLite pilot

This opt-in pilot implements the birthday repository's domain operations with
SQLite, SQLAlchemy Core, aiosqlite and Alembic. The bot still constructs the JSON
repository. Nothing imports this package from runtime startup or cogs. Pilot
dependencies belong to the development group; exact versions are in `uv.lock`.

## Scope and decision

The current JSON store owns whole-document updates, file replacement, backup
rotation and physical-worker lifetime. The pilot evaluates replacing those
obligations with database transactions while preserving birthday models, method
results, delivery settings, member order and congratulation history. It does not
change scheduling, message delivery, Music, voice history, XP, or deployment.

The concrete correctness risk is concurrent operations overwriting each other,
or one task committing another task's transaction on a shared connection. Each
operation here checks out one connection for its entire transaction. There is
no application lock or custom transaction scheduler.

The implementation is deliberately isolated, not a runtime backend switch.
It adds code while both implementations coexist. No overall LOC reduction,
voice throughput improvement, or production readiness is claimed from this pilot.

## Reproduce

Run from the repository root after `uv sync --locked`. Use a dedicated directory
under ignored `data/`; do not point maintenance commands at production files.
Create `data/sqlite-pilot` first (`mkdir -p data/sqlite-pilot` on Linux).

```bash
uv run --locked python -m experiments.birthday_sqlite --database data/sqlite-pilot/birthdays.sqlite migrate
uv run --locked python -m experiments.birthday_sqlite --database data/sqlite-pilot/birthdays.sqlite import-json data/user_birthdays.json
uv run --locked python -m experiments.birthday_sqlite --database data/sqlite-pilot/birthdays.sqlite backup data/sqlite-pilot/backup.sqlite
uv run --locked python -m experiments.birthday_sqlite --database data/sqlite-pilot/restored.sqlite restore data/sqlite-pilot/backup.sqlite
```

All paths are explicit. Migration is an operator action, not an application
startup side effect. Alembic creates an empty database through the checked-in
revision, never `metadata.create_all()`. Future revisions must keep their own
schema definitions and be reviewed before use.

Import validates the complete JSON before writing, then imports all guilds in a
single transaction. Identical repeated imports insert nothing. A differing
existing guild aborts the whole import, including earlier inserts in that attempt.
The input JSON is never changed. Invalid records are rejected instead of skipped;
legacy date strings and history duplicates are retained. IDs must fit SQLite's
signed 64-bit integer range. Unknown JSON fields are outside the birthday model.

Backup uses SQLite's online backup API, including committed WAL content, followed
by `quick_check`. Backup and restore require a new destination and refuse to
overwrite any existing file. Restoring creates a separate database; it does not
switch the bot or replace an open file. Tests reopen that restored database through
the repository and compare its contents. SQLite integrity checking does not itself
validate application semantics or replace retaining backups on another medium.

## Connection and data contracts

- One application owner creates `open_engine(path)` and disposes it after its
  tasks finish. The explicit `AsyncAdaptedQueuePool` has `pool_size=1`,
  `max_overflow=0`, and a 10-second checkout timeout. SQLite's busy timeout is
  5 seconds. This bounds active connections, not the number of awaiting requests.
- Reads and writes both use `async with engine.begin()`. Never pass that
  connection to independent concurrent tasks. All pilot operations, including
  readers, serialize through this pool. Multiple independent application owners
  are not the supported pilot configuration; lock conflicts propagate as errors.
- Driver-managed implicit transactions are disabled with `isolation_level=None`.
  SQLAlchemy's `begin` event emits `BEGIN`, covering reads and DDL as well as DML.
  This documented approach avoids relying on an `autocommit` attribute exposed
  identically by sqlite3 and its async adapter.
- Migration enables WAL. Each connection enables foreign keys and FULL
  synchronous mode outside transactions. No unsafe durability pragmas are used.
- Guilds, members and ordered congratulation entries use separate tables.
  Cascading foreign keys remove dependent rows. Domain objects remain detached.
  `save` replaces an aggregate; semantic methods preserve unrelated state.
  Guild listing is ordered by ID; member and congratulation order are retained.
- Markers record already-sent messages, exactly as the existing repository does.
  A database transaction cannot make a Discord send and its marker atomic.
- Cancellation tests cover waiting for the pool and cancelling between statements
  inside an open transaction. They do not establish an unambiguous outcome when
  cancellation happens during commit, repeated cancellation during cleanup, or
  a power failure. An interrupted caller must not assume an attempted commit
  could not have succeeded.

Synchronous maintenance uses the existing `run_in_thread` helper at the CLI
boundary. Regular repository operations use the library's async API.

## Verification and limits

```bash
uv run --locked pytest -q tests/repositories/test_sqlite_birthday.py
uv run --locked basedpyright experiments/birthday_sqlite
uv run --locked ty check experiments/birthday_sqlite
```

The tests compare semantic operations with the JSON repository, exercise
concurrent mutations, duplicate markers, rollback, cancellation, import conflicts,
foreign keys, schema parity, restart, and backup/restore. They use synthetic data
and temporary files. `typing_contract.py` checks exact query, row and unpacked
result types, including `int | None`, using `assert_type` in both analyzers.
Concrete `Column` declarations avoid the descriptor typing differences encountered
with `Named` in the project's analyzer versions. No type suppressions or casts are
needed in the pilot.

Before any runtime adoption, integrate repository lifecycle with the application
and run the same tests on the target Linux host. A separate voice experiment must
measure batch persistence, profile reads, cancellation, connection wait and
cold/warm replay with real voice records. Birthday tests do not determine a suitable
voice pool size, cache revision policy, event schema, or XP semantics.

References: [SQLAlchemy SQLite transaction control](https://docs.sqlalchemy.org/en/21/dialects/sqlite.html),
[typed Core tables](https://docs.sqlalchemy.org/en/21/core/metadata.html),
[Alembic connection sharing](https://alembic.sqlalchemy.org/en/latest/cookbook.html),
[SQLite backup API](https://www.sqlite.org/backup.html).
