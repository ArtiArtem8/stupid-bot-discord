# Finite voice SQLite workload

This experiment follows the birthday pilot. It never changes bot startup,
production JSON/JSONL, Music, XP policy or the collector. Run it locally against
a frozen project `.tar.xz` archive. It reads only voice journal members and never
extracts arbitrary archive paths. New databases and backups stay under a new
explicit output directory.

## Question and boundaries

The question is whether short, owned SQLite transactions can retain the recorded
facts while batch ingestion, profile queries, birthday writes and backup contend.
The concrete risk is full-history readers delaying ingestion through a single
shared connection. Start with the birthday pilot's bounded queue, then compare
one separate query-only reader if measured contention warrants it.

There is exactly one application writer connection in both configurations.
`--read-pool` adds one independent reader connection with `PRAGMA query_only=ON`.
Each has its own `AsyncAdaptedQueuePool(size=1, max_overflow=0)`. Birthday writes
use the writer. Backup uses SQLite's online API through an additional short-lived
connection. No application transaction scheduler or lock is introduced.

The disposable `trial_*` schema is created explicitly for each run; it is not an
Alembic revision or a proposed production voice schema. Birthday tables still
come from their Alembic migration. Adoption requires its own versioned schema,
lifecycle and cutover work.

## Identity and equivalence

Facts use the existing voice codec and an insertion ordinal. No uniqueness is
imposed on `(boot_id, sequence)`: a loss marker can reuse that pair. Every input
fact and its order are retained. `monotonic` is normalized to float before
encoding, matching its decoded type and making pre/post-restart retries stable.

One caller-chosen operation token identifies an entire ordered batch. The token
and facts commit atomically. Reusing a token with the same canonical content is
a no-op; different content is rejected. Retries after an ambiguous commit use
the original token. Choosing a new token is a new operation, not deduplication.
Persisting/reconstructing those tokens in a future collector remains production
integration work. A payload hash alone does not identify an observation.

Archive reading matches journal scope/day/legacy-v2 order and gzip preference.
After each run, every decoded fact is compared with the frozen input. On the first
run of each configuration, replayed timelines, all four card read models, and
guild/global user summaries are compared exactly for every observed guild/user
pair, including bot and unknown identities. Summary windows and card clocks are
fixed from the recorded timestamps. No epsilon is applied to XP or other models.

Read transactions materialize wire payloads and their committed watermark, then
close. Decoding, timeline replay and card model building run afterwards in worker
threads. Native rendering, Discord HTTP and avatar loading are not benchmarked.

Tests cover retry conflicts, same-sequence loss records, scoped/shared reads,
cancellation while awaiting a connection, cancellation of an active streaming
read, and cancellation scheduled at the commit boundary. Either full commit or
full rollback is accepted there; retry must produce exactly one whole batch.
A fresh Python process reopens a committed database and repeats its token.
This is not a crash-at-every-instruction or power-loss proof.

## Reproduce on Windows

From the repository root, after `uv sync --locked`:

```powershell
uv run --locked python -m experiments.voice_sqlite --archive "$env:USERPROFILE\Downloads\stupid-bot-discord-2026-10-05_17-34-19.tar.xz" --output data/voice-trial-single --runs 3 --reads 3
uv run --locked python -m experiments.voice_sqlite --archive "$env:USERPROFILE\Downloads\stupid-bot-discord-2026-10-05_17-34-19.tar.xz" --output data/voice-trial-reader --runs 3 --reads 3 --read-pool
uv run --locked pytest -q tests/repositories/test_voice_sqlite_trial.py tests/repositories/test_voice_sqlite_archive.py
```

Existing output directories are rejected. The databases contain copied voice
facts; do not publish them. `results.json` contains aggregate metrics and archive
hash only. Timing is an explicit experiment, never a wall-clock CI assertion.

Each run primes 80% of the 26,947 recorded facts, then mixes the remaining 5,390
facts in eleven batches (maximum 500), two reader tasks with three reads each,
twenty birthday setting updates, and three backups. Reader contexts are the
first two sorted observed guild/user pairs; this is a bounded scenario, not
representative production request sampling. Ingestion follows the archived input
ordering at maximum offered speed, not the original real-time event spacing.

Checkout time includes connection creation when needed. Commit-context time
includes driver completion and event-loop scheduling, not just disk fsync time.
An uncached profile includes checkout, payload fetch, decode, replay and all four
models. A reused-timeline measurement rebuilds those models on the same detached
timeline; it is not a cached PNG hit or an OS-cache-flushed cold/warm comparison.

The 10 ms monitor estimates scheduling delay and samples WAL file size. Windows
timer granularity can produce zero values; these are approximate diagnostics.
The memory probe runs separately after timing with `tracemalloc`: it measures
peak newly traced Python allocations for one cold read, not process RSS, native
SQLite memory, or all retained application memory. Percentiles use nearest rank.
With six reads and eleven batches per run, p95 is effectively the maximum; do
not interpret it as a production service-level percentile.

Every backup must pass `quick_check`, `foreign_key_check`, the expected birthday
revision, and repository readability. Its voice content must match an entire
committed batch prefix. The general birthday backup command remains diagnostic
and can preserve inconsistent sources; these are separate admission checks.

## Environment

Verification was performed on Windows only. No Linux host, SSH or WSL connection
was used. Recorded runs include Python 3.12.14, SQLite 3.53.1, SQLAlchemy 2.1.3,
aiosqlite 0.22.1 and Alembic 1.20.0. Inspect the actual SQLite engine independently
of Python dependency auditing; this engine is newer than the documented WAL-reset
fix in SQLite 3.51.3. WAL, FULL synchronous mode, foreign keys, 5-second busy timeout
and automatic checkpoint settings are recorded in each result.

References: [SQLite WAL](https://www.sqlite.org/wal.html),
[SQLAlchemy pools](https://docs.sqlalchemy.org/en/21/core/pooling.html).

## Recorded result and decision

The preserved aggregate outputs are [single pool](results/windows-single-pool.json)
and [separate reader](results/windows-read-pool.json). Both configurations ran
three times against the same archive hash. The single-pool report was produced
before adding the optional reader flag; its absent flag means the baseline
configuration. All six runs retained exactly 26,947 facts with zero workload
errors. The first run of each configuration compared 32 observed contexts:
128 card models and 64 guild/global summaries, including exact XP. All eighteen
backups passed integrity, foreign-key, revision, readability and batch-prefix checks.

The following ranges are per-run nearest-rank p95 values in milliseconds, not
pooled production latency percentiles:

| Measurement | One shared connection | Writer + query-only reader |
| --- | ---: | ---: |
| Writer checkout | 1,879–1,925 | 955–1,001 |
| Commit context | 43–55 | 105–145 |
| Reader checkout | 1,062–1,112 | 51–63 |
| Uncached profile (four models) | 1,631–1,678 | 1,578–1,656 |
| Reused-timeline projection | 362–429 | 367–387 |
| Birthday operation | 1,843–1,911 | 1,696–1,846 |
| Event-loop scheduling delay | 22–25 | 36–37 |

Sampled WAL size stayed around 4.02 MiB in these finite runs, not a proof of
bounded WAL size for every workload. The separate allocation probe measured
approximately 12 MiB of newly traced Python allocations per selected cold read.
Native memory and existing retained objects are not included.

The extra reader reduces connection contention but does not remove the long
tail or materially accelerate full-history profile building, and other timings
worsen. This experiment does not isolate every contribution from driver round
trips, Python scheduling/GIL, query execution and background desktop activity.
Do not attribute the whole checkout tail to filesystem latency or declare the
second pool a universally better production setting.

Decision: keep the selected stack and proceed, on explicit authorization, with
the first production birthday integration and its lifecycle/import/rollback work.
The voice experiment supports fact preservation and batch retry semantics, but
does not justify replacing the production voice journal wholesale. The concrete
next voice boundary is reducing full-history reads/replay before choosing a
production reader pool. No new database contest or extra writer scheduler is
needed. No latency target was supplied; these measurements expose the trade-off
rather than certify a service-level objective. Linux performance remains untested
at the user's request.
