# Voice architecture

## Ownership and invariants

```text
Discord raw observation -> typed journal fact -> append-only journal
    -> pure timeline -> common scope -> pure metrics -> UI read model
```

- `cogs/voice/collector_cog.py` normalizes Discord observations, takes snapshots
  and records lifecycle boundaries. It never replays history or calculates metrics.
- `api/voice/model.py` defines immutable facts. Humans, bots and unresolved users
  share `VoiceStateSnapshot`; unknown fields remain `None`, not invented defaults.
- `repositories/voice_journal.py` owns one bounded queue, one writer and serialized
  file maintenance per root. `_voice_codec.py` owns wire encoding and legacy decoding.
- `api/voice/timeline.py` reconstructs history once, without Discord or file I/O.
  `scope.py` applies shared guild/channel/time selection to credited room intervals.
- `metrics/` contains pure projections. `queries.py` composes UI-facing values from
  one reusable timeline. UI code receives read models rather than raw JSONL.

A mutable journal root has one owner per process. Received, accepted into the
buffer and persisted are separate counts. Only a successful flushed and fsynced
append advances persisted. Shutdown stops acceptance and waits for the in-flight
batch and queued records; cancelling the caller does not cancel physical I/O.

## Journal schema v2

New records live under `data/voice_probe/v2/{session,guild_ID}/events_YYYY-MM-DD.jsonl`,
rotated by the UTC observation date. The writer emits only `schema_version: 2`.

| Kind | Payload beyond the envelope |
| --- | --- |
| `observation` | `state` |
| `snapshot` | `states`, explicit `authoritative` |
| `gap` | `started_at`, nullable `ended_at`, `reason`, `known_bounds` |
| `checkpoint` | Liveness only |
| `lifecycle` | `stopped` |

The envelope contains `schema_version`, `sequence`, `boot_id`, `observed_at`,
`monotonic`, `guild_id` and `kind`. A null guild denotes a global/session fact.
Collector observation timestamps are UTC; monotonic time detects clock changes.

State fields use full names: `user_id`, `channel_id`, `channel_known`, `is_bot`,
`self_mute`, `self_deaf`, `server_mute`, `server_deaf`, `self_stream`, `self_video`,
`suppress`, `requested_to_speak`, `requested_to_speak_at`, `session_id` and `afk`.
A known null channel means leave. An unresolved cache channel has
`channel_known=False`; its user ID is retained.

For raw Discord records, a requested-to-speak timestamp implies `True`, explicit
null means `False`, and an absent field remains unknown. A timestamp and `False`
are contradictory and rejected. The nullable boolean is an additive v2 field;
earlier v2 records still decode. An earlier v2 null timestamp alone cannot prove
`False`, because the old format conflated absence and explicit null.

AFK is derived from the raw channel ID when an AFK channel ID is available.
`GUILD_CREATE.afk_channel_id` takes precedence over resolved cache context, including
when the channel object is unavailable. Without an AFK channel ID the result is
unknown. No guild AFK configuration or user timezone is persisted separately.

## Timeline and observation quality

`VoiceTimeline` contains three independent immutable projections:

- `rooms`: half-open `RoomInterval` values with constant known states and any gaps
  intersecting that slice. Intervals for a guild/channel never overlap.
- `coverage`: positive, half-open `ObservationInterval` values for each guild,
  including periods when every voice room was empty.
- `gaps`: explicit `ObservationGap` values, including open-ended uncertainty.

Replay starts unknown. Only an authoritative full snapshot opens coverage, at its
own timestamp. An empty authoritative snapshot also opens coverage. A checkpoint
or local/non-authoritative snapshot cannot establish coverage or close a gap.
Thus an empty-room day with coverage differs from a day without observations.

Disconnects, clock discontinuities, boot changes and lost writes interrupt
coverage. A subsequent full snapshot restores only its guild from that timestamp
forward. A snapshot disagreeing with replay invalidates the interval since the
preceding authoritative snapshot: the time of a missing change is unknown.
Retrospective and overlapping gaps are subtracted from coverage as well as room
credit. Nothing is extrapolated beyond the last recorded observation.

Overflow markers retain the rejected record's guild and earliest known loss time.
The writer keeps one pending marker per affected guild, plus a separate global
marker when a global/session record is rejected. Loss in guild A does not invalidate
guild B. A write failure can have partially appended multiple files, so it remains
a conservative global gap. Failed batches are not retried or acknowledged as
persisted; `close()` raises even if later batches recover.

Read all relevant guild and session day files, including the preceding full
snapshot, before building a timeline. Within a boot, sequence wins over wall-clock
order. Boot groups use their earliest wall time; their true order cannot be
recovered exactly when clocks overlap. Readers raise on corruption or unknown
schemas rather than silently presenting lost facts as continuous observations.
Partial append damage may require manual recovery of a copy; the writer never
rewrites damaged source files.

## Scope and metric semantics

All metrics use the same scope and half-open time range. Global scope adds results
from matching guild histories; guild scope restricts the same computation to one
guild. Channel scope requires its guild. Global seconds are additive guild-seconds,
not the union of simultaneous activity across guilds.

Human co-presence counts elapsed seconds for a pair of known humans occupying the
same channel in the same guild. Global co-presence sums these overlaps across guilds;
it never pairs people in separate guilds or rooms. Each pair receives an interval's
seconds once, regardless of room size.

Private co-presence requires exactly two known humans, allows any number of known
bots, and excludes intervals with any unidentified participant. A third human is
not private. Bot co-presence is separate and implies nothing about music playback.
Solo presence similarly ignores known bots but cannot credit an unidentified room
occupant as absent. Presence sessions are contiguous observed visits per guild;
gaps, query clipping and changed known session IDs can split or truncate them.

Activity accepts a `tzinfo` projection parameter, defaulting to UTC. Arithmetic
uses real elapsed UTC seconds, while hours, weekdays and dates use the supplied
calendar. Repeated DST hours accumulate in the same bucket; skipped hours receive
no seconds. There are always 24 hourly and 7 Monday-first weekday buckets.
Timezone selection also flows through `user_summary`; no timezone storage is added.
XP remains a versioned display calculation, and graph edges remain projections.

## Legacy mapping and retention

Legacy files under the original journal root are read-only: never rewritten,
compressed or pruned by this implementation.

- Join/leave/move/flags/noop and bot_voice decode into the same state observation.
- Human and bot presence maps decode into snapshots with unknown flags. Missing
  either population map makes the snapshot incomplete; it cannot establish coverage.
- A heartbeat without maps becomes a checkpoint. Resume/drop/clock/drift markers
  become gaps; legacy drops/drift without a lower bound conservatively invalidate
  from the boot's first record.
- Abbreviated flags are decoded only when present. `hr` preserves the nullable
  requested-to-speak boolean without inventing a timestamp.

Absent mute/deaf/video/stream fields, session IDs, dropped user/channel IDs and
precise times of missing events cannot be recovered. Neither `hr=True` nor
`hr=False` reveals the original timestamp. Missing bot populations cannot prove
absence of bots.

`VOICE_PROBE_RETENTION_DAYS` defaults to `None`: raw history is retained indefinitely.
Compression of finished v2 days remains enabled and lossless. `prune(...,
retention_days=None)` performs no file I/O; only an explicitly supplied positive
number deletes v2 day files with a UTC date strictly before `today - retention_days`.
Legacy files are preserved regardless of retention. Without persistent aggregates,
explicit pruning necessarily limits future all-time statistics to retained history.

## Discord compatibility boundary

The real bot constructor passes the public Client option
`enable_debug_events=config.VOICE_PROBE_ENABLED`. The collector only listens to the
documented `on_socket_raw_receive` event; it does not mutate Client debug flags or
socket callbacks at runtime.

The only private Discord access in production voice code is `guild._voice_states`
in the cache snapshot method. Public per-channel state mappings omit unresolved
channels; the guild cache retains their user IDs. Such snapshots are explicitly
non-authoritative. A contract test constructs a real installed `discord.Guild`
with an unresolved member/channel and verifies this behavior.

The normal recursive cog loader discovers `cogs/voice/collector_cog.py` alongside
the other bot extensions. There is no separate legacy entry point. Bot startup
uses global command sync, restores uptime and starts activity/autosave tasks;
shutdown saves uptime and unloads the collector, draining its journal.

UI, new services, databases and persistent statistics caches remain outside scope.
