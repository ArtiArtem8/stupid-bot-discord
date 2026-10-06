# Schema and operation contracts

All candidate keys below functionally determine their row's non-key attributes.
Semicolons separate alternate candidate keys, commas form composite keys. SQL
NULL is unknown, not false/zero; absence of an optional feature row is meaningful.
Historical labels and constrained scope copies are deliberate denormalization.

| Table | Candidate keys | Kind | Lifecycle / additional dependency |
|---|---|---|---|
| `users` | `user_id` | Identity | Retained; directly observed username is freshness-checked, unknown metadata NULL |
| `guilds` | `guild_id` | Identity | Retained known guild context; observed name has timestamp |
| `guild_members` | `guild_id, user_id` | Identity/context | Retained after leave; not proof of current membership |
| `channels` | `channel_id` | Identity | Retained; guild may be unknown/DM NULL |
| `roles` | `role_id` | Identity | Retained scoped identity; deleted_us unknown unless observed |
| `storage_state` | `singleton` | Publication state | One row; BUILDING becomes COMPLETE only after import verification |
| `migration_sources` | `source_name` | Provenance | Immutable hashes/counts/formats for the imported snapshot |
| `birthday_settings` | `guild_id` | Current state | Versioned per-feature settings, independent of music |
| `member_birthdays` | `guild_id, user_id` | Current state | Versioned calendar date and source position; clear retains version |
| `birthday_history` | `guild_id, user_id, position` | History | Ordered duplicate-preserving markers; removed with birthday profile |
| `birthday_deliveries` | `guild_id, user_id, calendar_date; operation_id` | Delivery state | Claimed/uncertain/sent/obsolete; retained independently of birthday profile |
| `music_settings` | `guild_id` | Current state | Versioned 0–200 intent; absence uses configured default |
| `member_blocks` | `guild_id, user_id` | Current state | Explicit state and snapshot display hints |
| `block_events` | `event_id; guild_id, user_id, action, ordinal` | History | Append-only; legacy block/unblock lists retain separate order |
| `member_name_observations` | `guild_id, user_id, ordinal` | History | Append-only observed display labels, not global username history |
| `report_settings` | `singleton` | Current state | One global developer channel |
| `reports` | `report_id; position` | History | Append-only snapshots; nullable request_key unique only when present |
| `question_answers` | `user_id, normalized_question` | Current answer | Global user/question winner; no guild dependency |
| `monitor_settings` | `guild_id` | Current state | Versioned enable/TTL configuration |
| `role_snapshots` | `snapshot_id; guild_id, user_id` | Current snapshot | Atomically replaced; monotonically allocated ID fences stale deletion |
| `role_snapshot_roles` | `snapshot_id, position` | Snapshot child | Removed with snapshot; repeated role IDs retain positions |
| `uptime_periods` | `period_id` | Accumulating history | Current period updates; archived periods remain after reset |
| `runtime_checkpoint` | `singleton` | Current checkpoint | References active period; boot ID fences obsolete processes |
| `voice_batches` | `batch_id` | Publication history | Immutable content fingerprint/count/revision for retry resolution |
| `voice_records` | `record_id; batch_id, ordinal` | Raw fact | Append-only ingestion order, envelope and typed variant fields |
| `voice_record_states` | `record_id, position; record_id, user_id` | Raw fact child | Immutable nullable flags; zero rows represent an empty snapshot |
| `voice_revisions` | `guild_id` | Projection | Latest guild-specific committed global watermark |
| `voice_shared_revision` | `singleton` | Projection | Shared and total committed watermarks, updated atomically with facts |

## Uptime history

`uptime_periods.period_id` determines the accumulated duration, last checkpoint,
optional known start, and observed reset. `runtime_checkpoint` points at the
current period. Reset archives the previous period and changes the pointer in
one transaction. A short outage resumes the same period. The archive timestamp
is when the reset was observed, not an invented exact process-death time.
Elapsed duration adds the process's monotonic delta to the restored total;
checkpoint timestamps remain wall-clock UTC. A negative offline wall delta
resumes that total without claiming knowledge of elapsed offline time.

Read uptime history directly with SQL:

```sql
SELECT period_id,
       datetime(started_us / 1000000, 'unixepoch') AS started_utc,
       accumulated_us / 1000000.0 AS uptime_seconds,
       datetime(last_checkpoint_us / 1000000, 'unixepoch') AS last_checkpoint_utc,
       datetime(archived_us / 1000000, 'unixepoch') AS reset_observed_utc,
       reset_reason, origin
FROM uptime_periods ORDER BY period_id;
```

## Representation and normalization exceptions

IDs are positive signed 64-bit integers. Aware instants use exact UTC microseconds;
birthdays and delivery dates use ISO calendar dates. Imported naive report text is
kept separately; UTC may remain NULL. Legacy checkpoints do not establish a clean
shutdown. Snapshot labels never determine identity or rewrite older facts.

`channels.channel_id -> guild_id` and `roles.role_id -> guild_id`; member scope is
composite. Role/state child scope columns repeat the parent's guild only to enforce
same-guild foreign keys. Composite UNIQUE(parent_id, guild_id) constraints support
these references; they are superkeys rather than additional candidate keys.
Report user/guild/channel names and avatars, birthday display hints and role-leave
names are historical snapshots, not current
identity attributes. Report position preserves source order independently of IDs.
Voice record variant columns use CHECKs; payload JSON is not the fact store.

Uptime's current accumulated total appears in the checkpoint and active period;
one repository updates both in the same transaction. Voice revisions and birthday
delivery status are explicit publication/delivery state, not recomputed account
balances. `received/accepted/persisted/failed` remain queue telemetry. Voice cache
revision is max(shared watermark, guild watermark), each allocated from the same
monotonically increasing global commit counter. A batch affecting another guild
does not invalidate this scope unless it contains a shared fact.

## Queries and indexes

Profile materialization reads relevant guild and shared envelopes/states in one
snapshot transaction. Connection release precedes decoding/replay/XP/rendering.
Queries read full retained scope history. Cold admission is limited; cached
timelines reuse unchanged read revisions.

| Index | Lookup |
| --- | --- |
| `ix_members_user_guild` | Guild memberships by user |
| `block_events` unique key | One member's events ordered by action and ordinal |
| `role_snapshot_roles` primary key | Roles ordered by position within a snapshot |
| `ix_voice_scope_cursor` | Guild voice facts in ingestion order |
| `ix_voice_replay` | Facts by boot and sequence |
| `ix_voice_state_user_record` | Voice states by user and record |

Ordered scope scans grow with retained history. Child keys support parent lookup;
user/channel/admin indexes support reverse lookup and foreign-key checks.
