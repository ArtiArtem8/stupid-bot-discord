# Contributing to StupidBot

Keep the change focused. A small fix does not need to become an architecture rewrite.

## Setup

```bash
uv sync --locked --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
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
