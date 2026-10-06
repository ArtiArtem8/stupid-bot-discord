# Application SQLite storage

All durable features use one prepared database: birthdays, music volume, blocking
and its audit, reports, question answers, monitoring snapshots, uptime and voice
facts. `--database PATH` selects the file; default is `data/app.sqlite`. There are
no feature backend flags, JSON fallback, automatic migrations or startup imports.
SQLAlchemy Core, aiosqlite and Alembic remain the pinned runtime stack.

Feature implementations live in `repositories/<feature>_repository.py` and use
`<Feature>Repository` names. This `sqlite/` package owns only shared connections,
schema, identity operations and maintenance. `VoiceJournal` separately owns the
bounded ingestion queue; Music's `VolumeStore` protocol is its testable capability
boundary. Offline legacy codecs remain necessary for explicit imports. Completed
experimental backends are not part of the application tree.

## Prepare a local Windows copy

The following commands are maintenance only and do not start Discord. Use a
separate directory and immutable copies of old data. Do not run two application
owners against one file. A new empty installation is initialized explicitly:

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
checked again before COMPLETE publication. This option does not change runtime.

The importer accepts legacy JSON/JSONL/gzip sources only. To upgrade an existing
SQLite database, use the separate explicit `migrate` command on a copy.
Frozen revisions 0001/0002 are unchanged; 0003 preserves their saved birthday,
member, history and volume rows before replacing the old tables.

Old naive report timestamps retain their original text with unknown UTC time.
`--legacy-timezone Europe/Berlin` optionally interprets unambiguous local times;
DST folds/gaps remain unknown. Historical display hints do not become current
usernames. Imported uptime provides one checkpoint period with unknown start,
not an invented history or proof of clean shutdown.

The importer prints hashes, selected formats, counts, UTC observation bounds and
a manifest digest. It writes a new `.building.sqlite`, compares feature models,
ordered raw facts, timeline/coverage/gaps, guild/global exact XP and four read
models at the source horizon (UTC, 30-date detail windows), checks integrity/FKs, marks
COMPLETE, closes handles and checkpoints WAL before no-overwrite publication.
A failure leaves an unpublished file which startup rejects. Choose a new staging
path after diagnosing failure; there is no resumable-import framework. Repeating
an existing destination explicitly refuses without modifying it.

For a later authorized application launch, select the published file using
`main.py --database PATH`. This integration did not launch a live bot or modify
Linux, launchers, deployment or existing application data.

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

## Ownership and operation semantics

`StupidBot` constructs the feature owners and one `Database`. Preparation checks
revision and completion before restoring uptime or loading cogs. SQLAlchemy's
queued pool has one connection, no overflow, a ten-second checkout timeout and a
five-second SQLite busy timeout. Explicit BEGIN, foreign keys and FULL synchronous
mode apply; migrations enable WAL. Increasing pool size requires revisiting
read-modify-write semantics. Multiple independent writers are not supported.

Transactions are short and own a connection for the complete domain operation.
SQL is released before Discord, renderer or voice replay work. Shutdown stops
startup/reload and producers, unloads cogs, drains the accepted voice queue, saves
final uptime while SQLite is open, rejects new DB admission, drains admitted
transactions/pool waiters and disposes. Repeated/cancelled close callers share the
same cleanup. Storage failures propagate; authorization fails closed.

Elapsed uptime uses the restored total plus a process-local monotonic delta for
both presence and saves. UTC wall time dates checkpoints and selects resume/reset
across processes. A negative offline wall delta preserves the confirmed total
without inventing offline duration. Reset history remains queryable in SQL.

Birthday date clearing compares member version and preserves its row/version.
There is no whole-feature delete/recreate API that could reuse a stale version.
Delivery has a unique member/day key,
rechecks settings/member versions and persists uncertain status before HTTP.
Only a pre-send version rejection creates `obsolete`, which a fresh current claim
can atomically replace with a new token. The old token cannot begin or finish that
replacement. `uncertain` and `sent` never rearm. Timeouts, crashes and interrupted
claims do not trigger blind resend. Block state
and audit commit together. Question conflicts return the committed winner and do
not consume the RAM answer queue on failure. Reports commit before acknowledgement;
interaction retries return the original report without another notification.

Monitoring replaces snapshots atomically, compares snapshot identity on deletion,
serializes member leave/restore, and rechecks before subsequent role requests.
Successful remote edits are not rolled back by a later SQL/HTTP failure. Volume
commands, join and healer share a per-guild ordering owner; healer playback PATCH
uses current stored intent. Neither mechanism holds SQL across HTTP. A network
mutation with an unknown outcome is not proof of a remote rollback.

Voice batches have stable retry keys plus content fingerprints; mismatches refuse.
Facts, states and revisions commit together. Revisions are independent of writer
telemetry and combine guild/shared watermarks. Nullable flags and empty snapshots
retain meaning, including equal-sequence loss markers. Cold profile reads/replay
have bounded admission; cancellation retains ownership until replay work finishes.
No retention, incremental projection or timeline coalescing is introduced here.

See [schema and manual uptime history queries](SCHEMA.md) for relational contracts.
