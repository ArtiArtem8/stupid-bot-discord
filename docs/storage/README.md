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
uv run --locked python -m stupidbot.storage --database "$localDb" migrate
uv run --locked python -m stupidbot.storage --database "$localDb" check
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

Stop the bot, make a backup using the old installation, then select the new code
and run `uv sync --locked --no-dev`. Prepare a separate database with that code:

```sh
uv run --locked --no-dev python -m stupidbot.storage --database data/app-backup.sqlite backup data/app-upgraded.sqlite
uv run --locked --no-dev python -m stupidbot.storage --database data/app-upgraded.sqlite migrate
uv run --locked --no-dev python -m stupidbot.storage --database data/app-upgraded.sqlite check
uv run --locked --no-dev main.py --database data/app-upgraded.sqlite
```

Migrations are explicit; ordinary startup only validates. Preserve the existing
`VOICE_PROFILE_TIMEZONE`. When adopting the language-settings release for an
existing installation, also perform the guild review below before starting.

Before the upgraded instance accepts new work, rollback means selecting the old
code and its untouched database. Once new records have been written, switching
back to an earlier backup loses those records. There is no schema downgrade or
automatic reverse transfer.

<a id="backup-restore-and-rollback"></a>

## Existing guild language rollout

This section describes the one-time language rollout for installations upgrading
from the older Russian-only behavior. Routine maintenance is covered above.

Before deploying the architecture/i18n upgrade, inventory every existing guild
and review explicit language overrides. Discord's guild locale can be a default
`en-US`; it does not establish the community's intended language. The proposed
`ru` preserves the old installation's public messages.

```powershell
uv run --locked python -m tools.language_rollout inventory --output guild-languages.json
```

This reads Discord REST with `DISCORD_BOT_TOKEN`, without opening SQLite,
connecting to the gateway, syncing commands, or sending messages. It refuses
to replace an existing output. Keep this operator inventory outside Git.
Review every guild's `language` (`ru`, `en` or `ko`) and set its `reviewed` flag to
`true`. Refresh the inventory if server membership changes before cutover.

Stop the bot, back up its database using SQLite backup, and migrate a new copy
to the current schema (`0005_korean`). Preserve the existing `VOICE_PROFILE_TIMEZONE` value;
an unset old value still means UTC. Preview the reviewed decisions against
that prepared copy, then apply them as a separate deployment operation:

```powershell
uv run --locked python -m tools.language_rollout seed --database prepared.sqlite --manifest guild-languages.json
uv run --locked python -m tools.language_rollout seed --database prepared.sqlite --manifest guild-languages.json --apply
uv run --locked python -m stupidbot.storage --database prepared.sqlite check
```

Preview writes nothing. Apply inserts only absent guild preference rows in one
transaction. It never overwrites existing language, timezone or revision,
including a deliberate null-language reset. Repeating apply is a no-op. The bot
must remain stopped until the prepared database and new code are selected
together. No automatic migration or rollout seed runs at startup.

For installations imported from JSON, the original files remain an offline
snapshot. They are not a dual-write rollback copy and do not receive new records.

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
