# Voice history and metrics

Voice history records what the bot observed. It does not infer activity while
the bot was offline or disconnected.

## Collection and storage

`VoiceCollectorCog` converts raw Discord observations and snapshots into facts.
`VoiceJournal` owns their queue, writer and file maintenance. `build_timeline()`
reconstructs history without Discord or file I/O; scoped queries calculate
presence, companions, activity and XP from that timeline.

Each journal root has one writer per process. Received, queued and persisted
counts are distinct: only a successful flushed and fsynced append advances the
persisted count. Shutdown drains admitted records. Cancelling a shutdown waiter
does not cancel the physical write.

New records use schema v2 and live under
`data/voice_probe/v2/{session,guild_ID}/events_YYYY-MM-DD.jsonl`, rotated by UTC
observation date. Global lifecycle records use the `session` scope.

| Kind | Payload |
| --- | --- |
| `observation` | One voice state |
| `snapshot` | Voice states and an explicit `authoritative` flag |
| `gap` | Start, optional end, reason and whether bounds are known |
| `checkpoint` | Liveness timestamp |
| `lifecycle` | Stopped flag |

Every record carries `schema_version`, `sequence`, `boot_id`, `observed_at`,
`monotonic`, `guild_id` and `kind`. A null guild denotes a global record. Wall
clock timestamps are UTC; monotonic time detects clock discontinuities. Within
a boot, sequence determines record order. Boots use their earliest wall time;
their true order cannot be recovered exactly when clocks overlap.

Voice states retain user and channel IDs, bot identity, mute/deaf/stream/video
flags, suppress, requested-to-speak state and timestamp, transport `session_id`,
and AFK status. Unknown fields remain `None`. A known null channel means leave;
`channel_known=False` means the channel is unresolved.

A raw requested-to-speak timestamp means `True`, explicit null means `False`,
and an absent field stays unknown. Earlier v2 records without the explicit
boolean still decode, but a null timestamp alone cannot establish `False`.
AFK comes from the channel ID and available guild AFK configuration; without that
configuration it stays unknown.

## Observed time and gaps

A timeline contains room intervals, observation coverage and gaps. Intervals are
half-open: their start is included and their end excluded. Only an authoritative
full snapshot opens or restores coverage for its guild. An empty snapshot also
establishes coverage; a checkpoint or cache snapshot does not.

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
It does not invalidate other guilds. A failed disk batch may partially write
multiple files, so it creates a conservative global gap. Failed batches are not
retried or counted as persisted; shutdown reports the failure even if later
batches recover.

Timeline reconstruction needs the relevant guild and session records, including
preceding full snapshots. Readers reject corruption and unknown schemas rather
than silently skipping facts. Recover damaged files from copies; the writer does
not rewrite them.

## Sessions and companions

A presence session is a continuous observed visit within one guild. Channel moves
and flag changes do not split it. Leaving, an observation gap or a bot restart
does. It is not a count of Discord transport session IDs or necessarily a count
of physical joins. A time-limited query can truncate a visit.

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

## Compression, retention and older records

Finished v2 days are compressed losslessly. `VOICE_PROBE_RETENTION_DAYS` defaults
to `None`, retaining all history. An explicit positive retention period deletes
v2 files dated strictly before `today - retention_days`. Without persistent
aggregates, pruning also limits future all-time statistics to retained history.

Legacy journals under the original root remain read-only. Their events map to
observations, presence maps to snapshots and heartbeat-only records to checkpoints.
Missing population maps cannot establish coverage. Legacy resume/drop/clock/drift
markers map to gaps; missing lower bounds conservatively invalidate from the
boot's first record. Missing flags, populations and event timestamps cannot be
recovered. Legacy files are never compressed or pruned by the v2 writer.

## Discord integration

The bot enables `on_socket_raw_receive` through the public `enable_debug_events`
client option when `VOICE_PROBE_ENABLED=true`. The collector's sole private API
access is `guild._voice_states`, needed to retain unresolved channel IDs in cache
snapshots. Those snapshots remain non-authoritative.

The ordinary Cog loader discovers the collector and profile command. Collector
shutdown drains the journal; profile shutdown drains media work and closes its
native renderer. See [profile cards](profile-card.md) for host setup and caching.
