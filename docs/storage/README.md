# Application SQLite storage

All durable features use one prepared database: birthdays, music volume, blocking
and its audit, reports, question answers, monitoring snapshots, uptime and voice
facts. `--database PATH` selects the file; default is `data/app.sqlite`.
Schema migrations are explicit; startup validates the prepared database.
SQLAlchemy Core, aiosqlite and Alembic versions are recorded in `uv.lock`.

Feature repositories share the engine and schema in this package.
See [tables, indexes and uptime history queries](SCHEMA.md) for data contracts.

## Prepare a local Windows copy

These commands create and validate a separate empty database without starting
Discord:

```powershell
$localDb = "$env:TEMP/stupid-local/app.sqlite"
New-Item -ItemType Directory -Force (Split-Path $localDb)
uv run --locked python -m stupidbot.storage --database "$localDb" migrate
uv run --locked python -m stupidbot.storage --database "$localDb" check
```

For offline JSON imports and one-time language seeding, see [LEGACY.md](../../LEGACY.md).

## Backup, restore and upgrades

Run these commands from the repository root. They work in PowerShell and a POSIX
shell. Replace the paths if the bot uses a different `--database` file. Choose a
new destination name for each operation; maintenance never overwrites a file.

### Back up a running installation

```sh
uv run --locked --no-dev python -m stupidbot.storage --database data/app.sqlite backup data/app-backup.sqlite
uv run --locked --no-dev python -m stupidbot.storage --database data/app-backup.sqlite check
```

Backup uses SQLite Online Backup and includes committed WAL contents. Never copy
just the main file of an active WAL database. A successful backup checks structure;
`check` also requires the current revision, COMPLETE state and valid foreign keys.
Backup may preserve a diagnostic copy with invalid foreign keys, so keep the check
step. Copy completed backups to separate storage if they must survive loss of the
bot's disk.

### Restore and verify a backup

Stop the bot and restore to a new file:

```sh
uv run --locked --no-dev python -m stupidbot.storage --database data/app-restored.sqlite restore data/app-backup.sqlite
uv run --locked --no-dev python -m stupidbot.storage --database data/app-restored.sqlite check
uv run --locked --no-dev main.py --database data/app-restored.sqlite
```

Restore validates the copied file before returning. A failure never falls back to
empty data; keep the error and diagnose the selected backup. After startup, inspect
`/health` and a few known saved settings or profiles. Keep the previous database
until you have checked the restored instance, and update the service's database
path before its next start.

An older backup may need migration. Work on a new copy using the procedure below,
rather than altering the only backup.

### Upgrade the schema

Stop the bot, make a backup with the installed version, then select the target code
and run `uv sync --locked --no-dev`. Prepare a separate database with that code:

```sh
uv run --locked --no-dev python -m stupidbot.storage --database data/app-backup.sqlite backup data/app-upgraded.sqlite
uv run --locked --no-dev python -m stupidbot.storage --database data/app-upgraded.sqlite migrate
uv run --locked --no-dev python -m stupidbot.storage --database data/app-upgraded.sqlite check
uv run --locked --no-dev main.py --database data/app-upgraded.sqlite
```

Migrations are explicit; ordinary startup only validates. Keep
`VOICE_PROFILE_TIMEZONE` unchanged during schema upgrades.

Before the upgraded instance accepts new work, rollback means selecting the old
code and its untouched database. Once new records have been written, switching
back to an earlier backup loses those records. There is no schema downgrade or
automatic reverse transfer.

## Runtime contracts

`StupidBot` owns one `Database`. Startup validates the schema and COMPLETE state;
shutdown stops producers, drains accepted voice work, saves uptime, then drains
transactions and disposes the engine. Multiple independent writers are unsupported.

SQLAlchemy's pool has one connection, a ten-second checkout timeout and a
five-second SQLite busy timeout. Connections use explicit BEGIN, foreign keys
and FULL synchronous mode; migrations enable WAL. Transactions end before
Discord calls, voice replay or rendering. Changing pool size requires reviewing
read-modify-write concurrency.

Birthday delivery claims are unique per member/day. `obsolete` means delivery
never started and can be reclaimed with a new operation ID. Pre-send failures
release only their own `claimed` operation; startup releases remaining `claimed`
rows before producers run. `uncertain` and
`sent` prevent automatic resend, including after restart. Logs identify failed
operations; inspect `birthday_deliveries` to resolve ambiguous outcomes.

Reports commit before Discord notification. Repeated interactions return the
saved report without another notification. Blocking and its audit commit
together; question retries preserve the committed answer. Role restoration and
music volume reject stale local operations; a failed remote request can still
have changed Discord or Lavalink state.

See [voice history](../../docs/voice/architecture.md) for observation gaps,
retry keys and read revisions, and [schema](SCHEMA.md) for uptime periods.
