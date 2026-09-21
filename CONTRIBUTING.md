# Contributing to StupidBot

Keep the change focused. A small fix does not need to become an architecture rewrite.

## Setup

```bash
uv sync --locked
uv run pre-commit install
```

## Normal Workflow

- Make a focused branch.
- Keep changes scoped to the thing you are fixing.
- Add or update tests when behavior changes.
- Let the installed hooks run before pushing.

## Full Check

Run the full pre-push hook set when you need a clean local pass:

```bash
uv run pre-commit run --all-files --hook-stage pre-push
```

Individual tools like Ruff, Basedpyright, ty, or pytest may still be run directly when debugging a failed hook.

## Commit Messages

Use imperative messages with a reasonable scope, like `Fix birthday reminder timezone`.

## Voice journal design note

The prototype mixes observation, replay, queue ownership and analytics in the
collector. It can lose an in-flight batch during shutdown and confuse accepted
records with persisted records. The smallest useful separation keeps JSONL,
daily rotation, bounded buffering and Discord lifecycle collection, but assigns
each invariant to one owner. UI, music integration and temporary startup/dev
sync changes are outside this refactor.

- `VoiceStateSnapshot` is the immutable state of either a human or a bot.
  Missing flags and bot knowledge remain unknown. Raw IDs, session ID and the
  requested-to-speak timestamp survive normalization.
- `VoiceJournalRecord` carries a boot ID, sequence, UTC observation timestamp,
  monotonic time and a typed observation, authoritative snapshot, local
  checkpoint, lifecycle marker or gap payload. New records have
  `schema_version: 2` and full field names. They live under `v2/` inside the
  existing journal root; legacy files are never rewritten or maintained.
- The journal owns the bounded queue and batching. Submission reports buffer
  acceptance, not persistence. Counters distinguish received, accepted and
  persisted records; only a successful flushed and fsynced append advances the
  last counter. Shutdown stops acceptance and awaits the writer, including its
  in-flight batch. Failed batches are not silently acknowledged or retried after
  a possibly partial append; the loss opens a write-failure gap.
- The pure timeline replays records once into immutable half-open room
  intervals with complete known states, plus separate `ObservationGap` values.
  Intervals for a room never overlap. Metrics consume only intervals outside
  gaps, through shared guild/channel/time scope filtering. Session counts mean
  contiguous observed visits; query boundaries and gaps can truncate them.
- A disconnect, overflow, write failure, clock discontinuity or boot change
  opens uncertainty. Heartbeats and local checkpoints cannot close it. A full
  authoritative snapshot restores coverage for that guild only from its own
  timestamp forward. Replay never extrapolates beyond the last observation.
- The legacy decoder maps join/leave/move/flags/noop and bot_voice to the same
  state observation. Presence and bot_presence maps become snapshots with
  unknown flags; a heartbeat without maps stays a checkpoint. A legacy snapshot
  missing either population map is incomplete and cannot establish coverage. Abbreviated flags
  are decoded only when present. A legacy raised-hand boolean cannot recover
  its timestamp; missing session IDs, unknown members and discarded channel IDs
  cannot be recovered. Resume/drop/clock markers become conservative gaps.

`queries.py` combines pure metrics into UI read models. UI consumers receive
summaries, companion statistics and activity profiles, never JSONL records.
Adding a metric for an already recorded field changes only this read side.

Snapshot drift invalidates the window since the preceding authoritative snapshot:
the timestamp of an unrecorded change cannot be reconstructed. Legacy overflow
and drift markers with no lower bound conservatively invalidate from the boot's
first record. UTC is the current activity calendar; private co-presence means
exactly two known human occupants, with no bot or unidentified occupant.

The collector uses discord.py's documented `on_socket_raw_receive` event. Because
temporary bot construction is out of scope, it enables debug events inside its
lifecycle using a narrow private attribute access. Hot loading rebinds the socket
receive callback the same way discord.py does at connection creation. The guild
voice cache also needs private access to retain unresolved users. These are the
only Discord-specific adapters; their assumptions are covered by collector tests.
The old `cogs/voice_probe_cog.py` entry point remains for the temporary voice-only
loader and delegates to idempotent registration of the new collector.

Journal schema v2 uses the following envelope and payload fields:

- Envelope: `schema_version`, `sequence`, `boot_id`, `observed_at`, `monotonic`,
  `guild_id`, `kind`.
- `observation`: `state` with the complete `VoiceStateSnapshot` field names.
- `snapshot`: `states` and explicit `authoritative`.
- `gap`: `started_at`, nullable `ended_at`, `reason`, `known_bounds`.
- `checkpoint`: liveness only; `lifecycle`: a `stopped` flag.

Read all relevant guild and session day files, including the preceding full
snapshot, before calling `build_timeline`. Within a boot, sequence determines
order even across wall-clock rollback. Boot groups use their earliest wall time;
the actual ordering of boots whose clocks overlap cannot be recovered exactly.
A reader raises on corrupt JSONL or an unsupported schema rather than skipping
lost facts and presenting the surrounding time as continuously observed.
The writer never rewrites a damaged file; a partial append may require manual
recovery of a copy. `close()` raises if any batch failed, even after later recovery.
