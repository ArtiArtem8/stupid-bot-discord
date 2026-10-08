# Application SQLite storage

All durable features use one prepared database: birthdays, music volume, blocking
and its audit, reports, question answers, monitoring snapshots, uptime and voice
facts. `--database PATH` selects the file; default is `data/app.sqlite`. There are
no feature backend flags, JSON fallback, automatic migrations or startup imports.
SQLAlchemy Core, aiosqlite and Alembic versions are recorded in `uv.lock`.

Feature repositories share the engine and schema in this package.
See [tables, indexes and uptime history queries](SCHEMA.md) for data contracts.

## Prepare a local Windows copy

Run maintenance against a separate database and immutable source copies. These
commands do not start Discord. A new empty installation is initialized explicitly:

```powershell
$localDb = "$env:TEMP/stupid-local/app.sqlite"
New-Item -ItemType Directory -Force (Split-Path $localDb)
uv run --locked python -m repositories.sqlite --database "$localDb" migrate
uv run --locked python -m repositories.sqlite --database "$localDb" check
```

To migrate legacy stores instead, choose a NEW destination and an offline source
directory containing the named files listed below:

```powershell
uv run --locked python -m tools.migrate_storage_once --source "$env:TEMP/stupid-source-copy" --destination "$env:TEMP/stupid-local/imported.sqlite"
```

The importer reads `user_birthdays.json`, `music_volumes.json`,
`blocked_users.json`, `user_reports.json`, `user_answers.json`, `last_run.json`,
`guild_monitor/guild_*.json` and `voice_probe/{session,guild_*}` plus their `v2`
counterparts. Missing features are empty. It never recursively imports backups.
Literal duplicate keys, normalized ID collisions, invalid dates/IDs/volumes,
unknown voice schemas and contradictory channel/role guilds cause refusal.
Volume is 0–200, including saved zero; no clamping occurs. Participant order and
repeated birthday history/role entries are retained. Original bytes are unchanged.

By default, paired JSONL/gzip must decompress to identical bytes. The explicit
`--allow-voice-prefix` import option additionally accepts a JSONL that ends at a
newline and is an exact byte and decoded-record prefix of its gzip. Both files
must decode successfully. The complete gzip is selected once; records are never
merged or deduplicated. Divergence, reordered records and torn lines still refuse.
The manifest records both hashes/counts, the selection rule and selected path;
`migration_sources` retains the same source provenance. Every source hash is
checked again before COMPLETE publication.

The importer accepts legacy JSON/JSONL/gzip sources only. To upgrade an existing
SQLite database, use the separate explicit `migrate` command on a copy.
Revision 0003 preserves birthday, member, history and volume rows from 0001/0002.

Old naive report timestamps retain their original text with unknown UTC time.
`--legacy-timezone Europe/Berlin` optionally interprets unambiguous local times;
DST folds/gaps remain unknown. Historical display hints do not become current
usernames. Imported uptime provides one checkpoint period with unknown start.

The importer prints hashes, selected formats, counts, UTC observation bounds and
a manifest digest. It writes a new `.building.sqlite`, compares feature models,
ordered raw facts, timeline/coverage/gaps, guild/global exact XP and four read
models at the source horizon (UTC, 30-date detail windows), checks integrity/FKs,
marks COMPLETE, closes handles and checkpoints WAL before publication.
Failures retain staging for diagnosis. Choose a new staging path after diagnosing
failure. Existing destinations are never overwritten.
Publication requires same-filesystem hard links and fails explicitly if unsupported.
If interrupted after linking, both names may reference the verified database;
after confirming they are the same file, remove only the staging name. A failed
publication can leave a COMPLETE staging database; its filename is not a schema guard.

Start the bot with `main.py --database PATH` to use the published database.

## Backup, restore and rollback

```powershell
uv run --locked python -m repositories.sqlite --database "$localDb" backup "$env:TEMP/stupid-local/backup.sqlite"
uv run --locked python -m repositories.sqlite --database "$env:TEMP/stupid-local/restored.sqlite" restore "$env:TEMP/stupid-local/backup.sqlite"
```

Backup uses SQLite Online Backup, including committed WAL contents, and refuses
an existing destination. It checks structure, but can retain a diagnostic copy
with invalid foreign keys. Restore/check additionally require current revision,
COMPLETE state and valid FKs; failed validation never falls back to empty data.
An older backup needs explicit `migrate` on its new copy before current startup.
Never copy just the main file of an active WAL database.

Before cutover, rollback means the old code and untouched old data. After new
SQLite writes, reverting to those JSON snapshots loses the new records unless a
separate reverse transfer is implemented. There is no dual-write rollback copy,
no automatic repair and no downgrade from the normalized schema.

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
