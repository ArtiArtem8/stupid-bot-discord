"""Run a Windows/Linux-local experiment against a frozen project tar archive."""

import argparse
import asyncio
import json
import platform
import sqlite3
import sys
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from experiments.voice_sqlite.archive import read_archive
from experiments.voice_sqlite.workload import run_once
from utils.asyncio_utils import run_in_thread
from utils.json_types import JsonObject, JsonValue


class Arguments(argparse.Namespace):
    archive: Path = Path()
    output: Path = Path()
    runs: int = 3
    reads: int = 3
    read_pool: bool = False


async def _run(args: Arguments) -> None:
    await run_in_thread(lambda: args.output.mkdir(parents=True, exist_ok=False))
    records = await asyncio.to_thread(read_archive, args.archive)
    if len(records) < 2:
        raise ValueError("The mixed workload requires at least two facts")
    results: list[JsonValue] = []
    report: JsonObject = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "sqlalchemy": version("sqlalchemy"),
        "aiosqlite": version("aiosqlite"),
        "alembic": version("alembic"),
        "archive_sha256": await asyncio.to_thread(
            lambda: sha256(args.archive.read_bytes()).hexdigest()
        ),
        "runs": results,
        "separate_query_only_reader": args.read_pool,
    }
    for index in range(args.runs):
        directory = args.output / str(index)
        await run_in_thread(directory.mkdir)
        results.append(
            await run_once(
                records,
                directory,
                args.reads,
                verify=index == 0,
                separate_reader=args.read_pool,
            )
        )
        await run_in_thread(
            lambda: (args.output / "results.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        )
        sys.stdout.write(f"Completed run {index + 1}/{args.runs}\n")
        sys.stdout.flush()


def main() -> None:
    """Require a new output directory; all mutable files stay inside it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--reads", type=int, default=3)
    parser.add_argument("--read-pool", action="store_true")
    args = parser.parse_args(namespace=Arguments())
    if args.runs < 1 or args.reads < 1:
        parser.error("runs and reads must be positive")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
