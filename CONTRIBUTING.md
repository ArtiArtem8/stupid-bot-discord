# Contributing to StupidBot

Keep the change focused. A small fix does not need to become an architecture rewrite.

## Setup

```bash
uv sync --locked
uv run --locked prek install --force
```

## Normal Workflow

- Make a focused branch.
- Keep changes scoped to the thing you are fixing.
- Add or update tests when behavior changes.
- Let the installed hooks run before pushing.

`uv run main.py --watch` reloads a loaded extension when its entry file changes.
It does not track imported shared modules or assets. Use a controlled restart
for those changes in production; the watcher does not provide system-wide hot reload.

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

Voice Profile Studio is a separate design/QA project. Approved runtime assets are
ported explicitly; its editor, benchmarks and parity suite do not run inside the
bot. See [profile card maintenance](docs/voice/profile-card.md) for the native
smoke check and asset update procedure.
