"""Export an auditable Git source snapshot without runtime data or credentials."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

_EXCLUDED_DIRS = {
    ".git",
    ".codex",
    ".claude",
    ".cursor",
    ".ssh",
    ".aws",
    ".azure",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "data",
    "backups",
    "logs",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "profiles",
    "dist",
    "build",
}
_EXCLUDED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".prof",
    ".pyc",
}


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    parts = tuple(part.lower() for part in path.parts)
    if not parts or path.is_absolute() or ".." in parts or "\\" in name:
        return False
    if any(part in _EXCLUDED_DIRS for part in parts):
        return False
    leaf = parts[-1]
    return (
        path.suffix.lower() not in _EXCLUDED_SUFFIXES
        and not leaf.startswith(
            (".env", "id_rsa", "id_ed25519", "credential", "secret", "token")
        )
        and leaf not in {".pypirc", ".netrc", ".mcp.json"}
        and not leaf.endswith(("-wal", "-shm", "-journal"))
    )


def _git(
    root: Path, *args: str, data: bytes | None = None, index: Path | None = None
) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git is required to export sources")
    environment = os.environ.copy()
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(  # noqa: S603 - fixed executable and argument array, no shell
        [executable, "-C", str(root), *args],
        input=data,
        capture_output=True,
        check=True,
        env=environment,
    ).stdout


def _head_files(root: Path, commit: str) -> dict[str, tuple[str, bytes]]:
    selected: list[tuple[str, str, str]] = []
    for entry in _git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
        # Never dereference symlinks or enter submodules, even in committed mode.
        if kind == "blob" and mode in ("100644", "100755") and _allowed(name):
            selected.append((name, mode, blob))
    objects = "".join(f"{blob}\n" for _, _, blob in selected).encode("ascii")
    stream = BytesIO(_git(root, "cat-file", "--batch", data=objects))
    files: dict[str, tuple[str, bytes]] = {}
    for name, mode, _ in selected:
        size = int(stream.readline().split()[-1])
        files[name] = (mode, stream.read(size))
        stream.read(1)
    return files


def _worktree_files(
    root: Path, baseline: dict[str, tuple[str, bytes]], include: list[str]
) -> dict[str, tuple[str, bytes]]:
    names = set(baseline)
    for name in include:
        if not _allowed(name):
            raise ValueError(f"Excluded or unsafe include path: {name}")
        names.add(PurePosixPath(name).as_posix())
    files: dict[str, tuple[str, bytes]] = {}
    modes = _index_modes(root)
    for name in sorted(names):
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Source path escapes the project or is a symlink: {name}")
        if not path.exists() and name in baseline:
            continue
        if not path.is_file():
            raise ValueError(f"Expected an existing source file: {name}")
        fallback = baseline.get(name, ("100644", b""))[0]
        mode = _file_mode(path, modes.get(name, fallback))
        files[name] = (mode, path.read_bytes())
    return files


def _index_modes(root: Path) -> dict[str, str]:
    modes: dict[str, str] = {}
    for entry in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            mode, _, stage = metadata.decode("ascii").split()
            if stage == "0" and mode in ("100644", "100755"):
                modes[name.decode("utf-8")] = mode
    return modes


def _file_mode(path: Path, indexed_mode: str) -> str:
    # Windows has no executable permission bit; Git's index owns that metadata.
    if os.name == "nt":
        return indexed_mode
    return "100755" if path.stat().st_mode & 0o111 else "100644"


def _worktree_patch(
    root: Path,
    commit: str,
    baseline: dict[str, tuple[str, bytes]],
    files: dict[str, tuple[str, bytes]],
) -> bytes:
    # An isolated index makes the patch and archive describe the same captured
    # bytes, including explicit untracked files. The user's index is untouched.
    with tempfile.TemporaryDirectory(prefix="source-index-") as directory:
        index = Path(directory) / "index"
        _git(root, "read-tree", commit, index=index)
        entries: list[bytes] = []
        empty_hash = "0" * len(commit)
        for name in sorted(baseline.keys() - files.keys()):
            entries.append(f"0 {empty_hash}\t{name}\0".encode())
        for name, (mode, content) in files.items():
            if baseline.get(name) == (mode, content):
                continue
            blob = (
                _git(root, "hash-object", "-w", "--stdin", data=content)
                .decode()
                .strip()
            )
            entries.append(f"{mode} {blob}\t{name}\0".encode())
        _git(
            root,
            "update-index",
            "-z",
            "--index-info",
            data=b"".join(entries),
            index=index,
        )
        return _git(
            root,
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            commit,
            index=index,
        )


def export_source(
    root: Path,
    output: Path,
    *,
    worktree: bool = False,
    include: list[str] | None = None,
) -> None:
    """Create a new ZIP with source bytes, hashes and an optional binary patch.

    HEAD is the default authority. Worktree mode captures HEAD paths plus explicit
    includes; staged additions also require inclusion. Symlinks are omitted from
    HEAD and rejected in worktree paths. Runtime/credential paths are excluded.
    The temporary Git index leaves staging untouched; changed blobs may be added
    to Git's object store. Existing output files are never replaced.
    """
    if include and not worktree:
        raise ValueError("--include requires --worktree")
    root = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    baseline = _head_files(root, commit)
    files = _worktree_files(root, baseline, include or []) if worktree else baseline
    patch = _worktree_patch(root, commit, baseline, files) if worktree else None
    manifest = {
        "version": 1,
        "commit": commit,
        "mode": "worktree" if worktree else "HEAD",
        "deleted": sorted(baseline.keys() - files.keys()),
        "files": [
            {"path": name, "mode": mode, "sha256": hashlib.sha256(content).hexdigest()}
            for name, (mode, content) in sorted(files.items())
        ],
        "patch_sha256": hashlib.sha256(patch).hexdigest()
        if patch is not None
        else None,
    }
    with (
        output.open("xb") as destination,
        ZipFile(destination, "w", ZIP_DEFLATED) as archive,
    ):
        for name, (_, content) in sorted(files.items()):
            archive.writestr(f"source/{name}", content)
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        if patch is not None:
            archive.writestr("worktree.patch", patch)


class Arguments(argparse.Namespace):
    output: Path = Path()
    worktree: bool = False
    include: list[str] | None = None


def main() -> None:
    """Parse source export arguments and report actionable command failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worktree", action="store_true")
    parser.add_argument("--include", action="append", default=[], metavar="REPO_PATH")
    args = parser.parse_args(namespace=Arguments())
    try:
        export_source(
            Path.cwd(), args.output, worktree=args.worktree, include=args.include
        )
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Source export failed: {exc}\n")


if __name__ == "__main__":
    main()
