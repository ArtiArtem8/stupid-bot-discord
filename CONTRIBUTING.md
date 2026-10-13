# Contributing to StupidBot

## Setup

```bash
uv sync --locked
uv run --locked prek install --force
```

## Normal Workflow

- Make a focused branch.
- Keep changes scoped to the thing you are fixing.
- Update both `README.md` and `README.ru.md` when changing documented features or setup.
- Add or update tests when behavior changes.
- Let the installed hooks run before pushing.

`uv run --locked main.py --watch` reloads a loaded extension when its entry file changes.
It does not track imported shared modules or assets. Use a controlled restart
for those changes in production; the watcher does not provide system-wide hot reload.

## Code ownership

Runtime code lives in `stupidbot/`. `app/` owns startup, the explicit extension
list in `app/extensions.py`, message logging, and shutdown. `settings.py` keeps
environment settings and the repository-relative data root; `storage/` owns
the shared SQLite lifecycle and migrations. `common/` contains shared pure
helpers and static JSON reading, while `discord_ui/` owns shared interaction,
authorization, feedback, and pagination behavior.

Each directory under `features/` keeps its commands, models, presentation, and
repository together. Music separates `playback/`, `runtime/`, and `discord/`;
voice separates facts and analytics, `discord/`, and `profile/rendering/`.
SVG assets live beside their renderer; bundled fonts live in `stupidbot/assets/`.
Tests follow the same owners under `tests/features/`, `tests/app/`,
`tests/discord_ui/`, `tests/common/`, and `tests/storage/`.

Add each extension once to `stupidbot/app/extensions.py`. Folder scanning no
longer registers cogs. Imported modules and assets still require a restart.
`main.py` remains a supported clone-and-run entry; `python -m stupidbot` invokes
the same CLI. SQLite maintenance uses `python -m stupidbot.storage`; the previous
`python -m repositories.sqlite` entry remains a thin compatibility command.

Translations live in each owner's `locales/` directory. Runtime uses stdlib
gettext; Babel is a development dependency. Use readable English `msgid` text
and stable English `msgctxt` values describing meaning, not filenames or classes.
English translations equal their source text; Russian translations live in `ru.po`.
This source convention does not change the Russian audience fallback.
Translate complete phrases, with plural forms for counts and named placeholders
for values; keep user content, external answers and technical identifiers intact.
Parameterized messages have typed
methods in the owner's `messages.py`. Pass an explicit presentation context to
views and renderers; do not set a global language. Personal/ephemeral responses
resolve user preferences, while shared public responses resolve guild preferences.
Voice profiles use the requester's preferences regardless of visibility; the
`private` option changes delivery only. Fixed-offset choices use IANA `Etc/GMT`
keys internally; display them through `common.timezones.timezone_label` to avoid
exposing their reversed POSIX signs.

After changing message literals or PO translations, run
`uv run --locked python -m tools.catalogs`. Commit the PO, MO, index and digest
files together. `uv run --locked python -m tools.catalogs --check` verifies
extraction, English source consistency, placeholders, plural forms, duplicates
and reproducible compilation;
the pre-push gate includes this check. Startup verifies release digests before
loading handlers. A saved `/ask` answer is already rendered text and must never
be retranslated on retrieval.

Command metadata follows Discord's client locale. Message bodies use the
selected user/guild override, then that audience's Discord locale, then Russian.
Gateway DM messages have no locale, so they use the personal override or Russian.
The single bot-wide uptime presence stays Russian because Discord provides no
per-guild audience for it. Birthday scheduling still uses the host calendar.
Before an existing deployment adopts the new policy, follow the
[guild inventory and explicit seeding procedure](docs/storage/README.md#backup-restore-and-rollback).

## Full Check

Run the full pre-push hook set when you need a clean local pass:

```bash
uv run --locked prek run --all-files --stage pre-push
```

Individual tools like Ruff, Basedpyright, ty, or pytest may still be run directly when debugging a failed hook.

The full check runs pytest in up to six processes with `--dist worksteal`.
`uv run --locked pytest -q` runs tests sequentially.

Set `PREK_CONCURRENT_HOOKS=1` and `PYTEST_XDIST_AUTO_NUM_WORKERS=1` on machines
with limited resources.

## Commit Messages

Use a conventional prefix and an imperative summary, such as
`fix: correct birthday reminder timezone` or `refactor: simplify profile formatting`.

Voice subsystem invariants and schema: [architecture](docs/voice/architecture.md).

See [profile card maintenance](docs/voice/profile-card.md) for artwork updates
and the native rendering check.
