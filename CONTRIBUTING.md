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
