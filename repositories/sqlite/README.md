# Shared SQLite storage

`--sqlite DATABASE` selects one existing SQLite file for birthdays and music
volume. Without this switch both features retain their JSON paths. Blocking,
monitoring, reporting, uptime and voice storage are unchanged. SQLAlchemy Core,
aiosqlite and Alembic are runtime dependencies pinned by `uv.lock`.

This replaces the previous `--birthday-sqlite` switch. Maintenance now runs through
`python -m repositories.sqlite`; birthday import is named `import-birthdays`.
There is no independent engine, database file or runtime flag for music volume.

## Upgrade the existing local birthday database

Work only on the local Windows copy. Stop the application owner before migration
or import. Set `$localDb` to the **same file already holding the birthday data**;
do not create another database for volume. For example, with the earlier local
setup:

```powershell
$localDb = "$env:TEMP/stupid-birthday-local/birthdays.sqlite"
uv run --locked python -m repositories.sqlite --database "$localDb" backup "$env:TEMP/stupid-birthday-local/before-volume.sqlite"
uv run --locked python -m repositories.sqlite --database "$localDb" migrate
uv run --locked python -m repositories.sqlite --database "$localDb" import-volumes data/music_volumes.json
```

Backup refuses to overwrite an existing destination; choose a new filename for
another backup. Migration retains revision `0001_birthdays` unchanged and applies
`0002_music_volume` on top. The new table has no foreign key to birthdays: a guild
may use music without registering birthdays. Existing birthday settings, member
order and duplicate congratulation history entries remain intact.

For a new local database, first create its parent directory, run `migrate`, then
explicitly import both sources:

```powershell
uv run --locked python -m repositories.sqlite --database "$localDb" import-birthdays data/user_birthdays.json
uv run --locked python -m repositories.sqlite --database "$localDb" import-volumes data/music_volumes.json
```

To select the prepared database on a future authorized launch, pass
`--sqlite "$localDb"` to `main.py`. This selects these two stores only, not a
sandbox for the bot's other data. Startup checks the supported Alembic revision
before loading cogs. Missing files and incompatible schemas fail without implicit
creation, migration, import, or fallback to JSON. An old `0001` database must be
explicitly upgraded first.

Removing the switch returns both features to their original JSON files. SQLite
changes are **not** exported back automatically; those JSON files are historical
snapshots, not synchronized rollback copies. Keep a database backup before
switching. A consistent copy can be restored to a new filename:

```powershell
uv run --locked python -m repositories.sqlite --database "$env:TEMP/stupid-birthday-local/restored.sqlite" restore "$env:TEMP/stupid-birthday-local/before-volume.sqlite"
```

A pre-volume backup still requires `migrate` before current startup accepts it.
Backup uses SQLite's Online Backup API and `quick_check`, including committed WAL
content. It may read a live source; it never overwrites a destination or repairs
application data. Before selecting a restored file, check foreign keys, schema
revision and repository readability as well as structural integrity.

## Import contracts

Both import commands decode and validate the complete source before writing,
then import all entries in one transaction. Repeating an identical import is a
no-op. An existing differing value aborts the entire attempt; no partial inserts
or implicit overwrite survive. Resolve differences explicitly in a separate
input copy before retrying. Source JSON bytes are never modified.

Literal duplicate object keys and different keys normalizing to the same guild
or member ID are rejected. IDs must be decimal strings fitting signed 64-bit
SQLite integers. Volume accepts integer values, integer strings and finite
integral floats; booleans, fractional values, malformed entries and overflow
are rejected. Import never silently truncates or skips data. Storage preserves
integer volume values without adding a clamp; the command's existing 0–200 range
and missing-setting default remain unchanged. A saved zero remains zero.

Birthday identity includes member insertion order, not just dataclass equality.
Order is the source file's decoded order (the JSON writer sorts keys
lexicographically). Legacy date strings and repeated history array entries are
retained. A Discord send and its saved congratulation marker are still separate
operations; database transactions do not promise exactly-once message delivery.

## Ownership and shutdown

`StupidBot` selects repositories and owns one `Database` and engine. Birthday
commands, timer and confirmations use the selected manager. Music composition
receives the chosen `VolumeStore` and supplies that same instance to the service
and healer. It never creates a second persistence owner on cog reload.

Each operation enters `Database.transaction()`. The owner counts admitted
operations, including those waiting for SQLAlchemy's pool, until transaction exit.
Shutdown stops producers through cog cleanup, rejects new database operations,
waits for admitted operations to release their transactions, then disposes the
engine. A cancelled close caller still waits for cleanup. This is resource
lifetime tracking, not a transaction scheduler; connection scheduling remains in
SQLAlchemy. Runtime code must not bypass this owner with raw engine access.

The queued pool remains `pool_size=1`, `max_overflow=0`, with 10-second checkout
and 5-second SQLite busy timeouts. It serializes readers and writers. Transactions
use explicit SQLAlchemy BEGIN hooks; migration enables WAL and connections enable
foreign keys and FULL synchronous mode. Changing pool size requires revisiting
read-modify-write concurrency, not merely performance tuning.

## Verification and limits

Tests use temporary Windows files and offline application composition. They cover
upgrading a populated `0001` database, birthday preservation, volume parity with
JSON, default and zero values, mixed mutations, rollback, duplicate/conflicting
imports, backup of both stores, and shutdown during an unfinished volume commit.
The shutdown test also checks rejected late birthday access, cancelled close,
physical connection closure and persisted data after application recreation.
Existing birthday and music behavior tests remain in the combined local gate.

The shared decoder retains one `cast(object, ...)` to narrow the standard JSON
loader's result before validation. Repositories need no type suppressions. This
work makes no claim of lower total code complexity while both backends coexist,
nor of improved voice analytics. No live bot, Linux host, deployment, or new
performance experiment is part of this integration.
