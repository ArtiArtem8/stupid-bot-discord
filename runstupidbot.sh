#!/usr/bin/env sh
set -eu

# Resolve this script's directory so the bot can be started from any cwd.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

run_bot() {
    if [ -x ".venv/bin/python" ]; then
        echo "Using Python virtual environment: .venv"
        ".venv/bin/python" main.py
        return $?
    fi

    if command -v uv >/dev/null 2>&1; then
        echo "No .venv/bin/python found; running through uv."
        uv run main.py
        return $?
    fi

    echo "ERROR: Neither .venv/bin/python nor uv was found." >&2
    echo "Run 'uv sync' first, or create a Python 3.12 virtual environment in .venv." >&2
    return 1
}

while :; do
    printf '%s\n' "=============================================="
    printf '%s\n' "        Starting the application"
    printf '%s\n\n' "=============================================="

    set +e
    run_bot
    exit_code=$?
    set -e

    if [ "$exit_code" -ne 0 ]; then
        printf '\nApplication exited with error code %s.\n' "$exit_code"
    else
        printf '\nApplication stopped normally.\n'
    fi

    printf 'Do you want to restart the application? [y/N] '
    read -r answer || exit "$exit_code"
    case "$answer" in
        [Yy]|[Yy][Ee][Ss]) ;;
        *) exit "$exit_code" ;;
    esac
done
