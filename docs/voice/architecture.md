# Voice history and metrics

Voice history records what the bot observed. It does not infer activity while
the bot was offline or disconnected.

## Collection and storage

`VoiceCollectorCog` converts raw Discord observations and snapshots into facts.
`VoiceJournal` owns their queue and writer; `VoiceRepository` commits typed facts
through the shared application Database. `build_timeline()` reconstructs history
without Discord or storage I/O; scoped queries calculate
presence, companions, activity and XP from that timeline.

One queue owner feeds the shared database. Received, accepted and persisted counts
are distinct: only acknowledged committed batches advance persisted telemetry.
A separate scoped read revision changes atomically with published facts. Shutdown
drains accepted records before engine disposal; cancellation of its caller does
not release ownership of the write. SQL batch IDs resolve identical retries and
reject changed content. The runtime has one schema, governed by Alembic.

| Kind | Payload |
| --- | --- |
| `observation` | One voice state |
| `snapshot` | Voice states and an explicit `authoritative` flag |
| `gap` | Start, optional end, reason and whether bounds are known |
| `checkpoint` | Liveness timestamp |
| `lifecycle` | Stopped flag |

Every record carries `sequence`, `boot_id`, `observed_at`,
`monotonic`, `guild_id` and `kind`. A null guild denotes a global record. Wall
clock timestamps are UTC; monotonic time detects clock discontinuities. Within
a boot, sequence determines record order. Boots use their earliest wall time;
their true order cannot be recovered exactly when clocks overlap.

Voice states retain user and channel IDs, bot identity, mute/deaf/stream/video
flags, suppress, requested-to-speak state and timestamp, transport `session_id`,
and AFK status. Unknown fields remain `None`. A known null channel means leave;
`channel_known=False` means the channel is unresolved.

A raw requested-to-speak timestamp means `True`, explicit null means `False`,
and an absent field stays unknown. The offline importer maps earlier records
without the explicit boolean to unknown; a null timestamp alone cannot establish
`False`.
AFK comes from the channel ID and available guild AFK configuration; without that
configuration it stays unknown.

## Observed time and gaps

A timeline contains room intervals, observation coverage and gaps. Intervals are
half-open: their start is included and their end excluded. Only an authoritative
full snapshot opens or restores coverage for its guild. An empty snapshot also
establishes coverage; a checkpoint does not. A cache snapshot is authoritative
only when all channel identities are resolved.

Replay retains every checkpoint for clock and coverage checks but does not split
unchanged rooms at each heartbeat. Adjacent intervals with the same complete
member states are coalesced; finalized gaps still split them at their exact
bounds. This analytical representation does not remove any stored facts.

Voice changes split intervals at their observation timestamps. Disconnects,
clock changes, boot changes and lost writes interrupt coverage. A full snapshot
that disagrees with replay invalidates time since the preceding authoritative
snapshot, because the missing change's timestamp is unknown. A later snapshot
restores coverage from its own timestamp; it does not fill the missing period.

Transport `session_id` differences alone do not establish drift: Discord's cache
may retain an older ID than the raw Gateway event. Both values remain diagnostic
telemetry. Gaps are removed from room time and coverage, including retrospective
and overlapping gaps. Nothing is extrapolated after the final observation.

Queue overflow records the affected guild and earliest known lost timestamp.
It does not invalidate other guilds. A failed/uncertain batch creates a
conservative global gap; its SQL envelopes, states and revision are atomic.
Failed queue batches are not retried or counted as persisted; shutdown reports
the failure even if later batches recover.

Timeline reconstruction needs the relevant guild and session records, including
preceding full snapshots. Startup rejects incompatible database revisions; readers
reject invalid stored variants.

## Sessions and companions

A presence session groups observed visits within one guild. Channel moves,
midnight and flag changes do not split it. A return after less than five minutes
continues the session only if guild observation covered the whole absence.
Exactly five minutes, an observation gap or a bot restart splits it. Absent time
never contributes to voice time, XP, average or median session duration. This is
not a count of Discord transport session IDs or physical joins. A time-limited
query can truncate a visit.

Companion time counts overlap between known humans in the same guild and channel.
Every pair receives the interval once, regardless of room size. Exactly two
known humans count as private co-presence; known bots are allowed, unidentified
participants are not. Solo presence also ignores known bots, but unidentified
occupants prevent proving that a human was alone. Bot co-presence does not prove
music playback.

Global presence and companion seconds add matching guild histories; they do not
deduplicate simultaneous activity across guilds. XP instead uses the highest
simultaneous room rate; see [progression](progression.md). Channel scope requires
its guild. All metrics use the same selected half-open time range.

Activity arithmetic uses elapsed UTC seconds, while hour, weekday and date
buckets use the requested timezone, default UTC. Repeated DST hours accumulate
in one bucket; skipped hours receive no time.

## Retention and older records

SQLite facts are retained indefinitely. The one-shot importer reads the original
and v2 JSONL/gzip formats
from offline copies; it rejects conflicting alternate files and unknown schemas.
Legacy events map to observations, presence maps to snapshots and heartbeats to
checkpoints. Missing population/flags remain unknown. Resume/drop/clock/drift
markers map to conservative gaps. The original files remain untouched. See [storage operations](../../repositories/sqlite/README.md).

## Discord integration

The bot enables `on_socket_raw_receive` through the public `enable_debug_events`
client option when `VOICE_PROBE_ENABLED=true`. The collector's sole private API
access is `guild._voice_states`, needed to retain unresolved channel IDs in cache
snapshots. Unresolved channel IDs make a snapshot non-authoritative.

The ordinary Cog loader discovers the collector and profile command. Collector
shutdown drains the journal; profile shutdown drains media work and closes its
native renderer. See [profile cards](profile-card.md) for host setup and caching.

## Process-local analytics

`StupidBot` owns `VoiceAnalytics` across collector reloads. It restores all scopes
before producers start. `VoiceJournal` submits runtime writes through this owner;
confirmed commits update `VoiceReplayState`, while unsafe ordering requests a
canonical rebuild. Raw facts remain in SQLite; no derived SQL tables are added.

A single lock excludes readers during commit/apply and snapshot creation.
Recovery first builds from a consistent SQL cutoff outside the lock, then catches
up to a fixed cutoff under it. An unsafe tail causes a full rebuild while writers
wait in the existing journal queue. Dirty state is never published as current.
A derived failure cannot turn a successful commit into a failed journal write.

Snapshots finish detached containers. Gap candidate windows restrict intersection
work while retaining source metadata order; long or open gaps can still require
broad scans. Shutdown drains the journal and analytics workers before closing SQL.
