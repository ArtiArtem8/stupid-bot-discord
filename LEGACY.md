# Legacy migration procedures

These procedures cover offline JSON stores and the one-time transition from
Russian-only public messages to per-guild language preferences.
For a new installation, use [INSTALLATION.md](INSTALLATION.md). Routine SQLite
preparation, backups, restores and upgrades are in the
[storage guide](docs/storage/README.md).

## Import JSON stores

Stop the bot that writes the source files and make immutable offline copies.
Run the commands from the repository root against those copies, using a new
destination. The example uses PowerShell:

```powershell
New-Item -ItemType Directory -Force "$env:TEMP/stupid-local"
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
Imported block and unblock lists preserve their separate order.

By default, paired JSONL/gzip must decompress to identical bytes. The explicit
`--allow-voice-prefix` import option additionally accepts a JSONL that ends at a
newline and is an exact byte and decoded-record prefix of its gzip. Both files
must decode successfully. The complete gzip is selected once; records are never
merged or deduplicated. Divergence, reordered records and torn lines still refuse.
The manifest records both hashes/counts, the selection rule and selected path;
`migration_sources` retains the same source provenance. Every source hash is
checked again before COMPLETE publication.

The importer accepts JSON/JSONL/gzip sources only. To upgrade an existing
SQLite database, use the separate explicit
[`migrate` procedure](docs/storage/README.md#upgrade-the-schema) on a copy.
Revision 0003 preserves birthday, member, history and volume rows from 0001/0002.

Report timestamps without a timezone retain their original text with an unknown
(`NULL`) UTC timestamp. `--legacy-timezone Europe/Berlin` optionally interprets
unambiguous local times; DST folds/gaps remain unknown. Historical display hints
do not become current usernames. Imported uptime provides one checkpoint period
with unknown start; that checkpoint does not establish a clean shutdown.

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
The original JSON files remain an offline snapshot. They are not a dual-write
rollback copy and do not receive new records.

### Imported voice records

The one-shot importer reads the original and v2 JSONL/gzip formats from offline
copies; it rejects conflicting alternate files and unknown schemas.
Legacy events map to observations, presence maps to snapshots and heartbeats to
checkpoints. Missing population/flags remain unknown. Resume/drop/clock/drift
markers map to conservative gaps. The original files remain untouched.

Records without the explicit requested-to-speak boolean map to unknown;
a null timestamp alone cannot establish `False`.
See [voice analytics](docs/voice/architecture.md) for current observation,
coverage and replay semantics.

## One-time guild language transition

For installations upgrading from Russian-only public messages, inventory every
guild and review explicit language overrides before starting the updated bot.
Discord's guild locale can be a default `en-US`; it does not establish the
community's intended language. The proposed `ru` preserves Russian public messages.

```powershell
uv run --locked python -m tools.language_rollout inventory --output guild-languages.json
```

This reads Discord REST with `DISCORD_BOT_TOKEN`, without opening SQLite,
connecting to the gateway, syncing commands, or sending messages. It refuses
to replace an existing output. Keep this operator inventory outside Git.
Review every guild's `language` (`ru`, `en` or `ko`) and set its `reviewed` flag to
`true`. Refresh the inventory if server membership changes before applying it.

Stop the bot, [back up its database and migrate a new copy](docs/storage/README.md#backup-restore-and-upgrades)
to the current schema (`0005_korean`). Keep `VOICE_PROFILE_TIMEZONE` unchanged;
an unset value means UTC. Preview the reviewed decisions against that prepared
copy, then apply them:

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
