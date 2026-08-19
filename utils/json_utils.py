import json
import secrets
import shutil
import time
from datetime import datetime
from os import PathLike
from pathlib import Path
from string import ascii_letters, digits

from config import BACKUP_DIR, ENCODING
from utils.json_types import (
    JsonEncodableObject,
    JsonObject,
    freeze_json_object,
    is_json_object,
)


def _generate_backup_filename(filename: Path, dt: datetime | None = None) -> str:
    """Build a collision-resistant backup name using a local wall-clock time.

    The format is ``<stem>_<YYYYmmdd_HHMMSS><four random chars><suffix>``.
    Passing ``dt`` makes the timestamp deterministic for tests; the random suffix
    is always generated with :mod:`secrets`.
    """
    now = dt or datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    rand_suffix = "".join(secrets.choice(ascii_letters + digits) for _ in range(4))
    return f"{filename.stem}_{timestamp}{rand_suffix}{filename.suffix}"


def _create_backup(
    filename: str | PathLike[str],
    max_backups: int = 3,
    backup_dir: Path | None = None,
) -> None:
    """Copy an existing file into the backup directory with bounded retention.

    Old backup deletion is best-effort. Copying is retried three times with a
    short blocking backoff; callers must move this function off an event loop.

    Raises:
        OSError: If the backup cannot be copied after all retries.
    """
    target_dir = backup_dir or BACKUP_DIR
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(filename)
    backup_name = _generate_backup_filename(filename)
    backups = target_dir.glob(f"{filename.stem}_*{filename.suffix}")
    sorted_backups = sorted(backups, reverse=True, key=lambda x: x.stat().st_mtime)
    for old in sorted_backups[max_backups:]:
        try:
            old.unlink()
        except OSError:
            pass

    for i in range(3):
        try:
            shutil.copy(filename, target_dir / backup_name)
            break
        except OSError:
            if i < 2:
                time.sleep(0.1 * (i + 1))
            else:
                raise


def get_json(
    filename: str | PathLike[str], encoding: str = ENCODING
) -> JsonObject | None:
    """Read and validate one JSON object from disk.

    A missing file returns ``None``. Existing unreadable, malformed, or non-object
    files raise instead of being treated as empty state.

    Args:
        filename: File to read.
        encoding: Text encoding used to decode the file.

    Returns:
        The decoded JSON object, or ``None`` when the path does not exist.

    Raises:
        OSError: If an existing file cannot be read.
        json.JSONDecodeError: If its contents are not valid JSON.
        ValueError: If the top-level JSON value is not an object.
    """
    path = Path(filename)
    if not path.exists():
        return None
    with path.open(encoding=encoding) as data_file:
        payload: object = json.load(fp=data_file)  # pyright: ignore[reportAny]
    if not is_json_object(payload):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def save_json(
    filename: str | PathLike[str],
    data: JsonEncodableObject,
    backup_amount: int = 3,
    backup_dir: Path | None = None,
    encoding: str = ENCODING,
) -> None:
    """Persist a JSON object with backup rotation and atomic replacement.

    The value is copied into built-in JSON containers before any file mutation.
    Existing files are backed up when ``backup_amount`` is positive. Data is
    written to a sibling staging file and then atomically replaces the target.
    The write path retries transient ``OSError`` failures three times.

    This is blocking physical I/O. Async callers should use
    :class:`utils.json_store.AsyncJsonFileStore`.
    """
    payload = freeze_json_object(data)
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and backup_amount > 0:
        _create_backup(path, backup_amount, backup_dir=backup_dir)

    temp_path = path.with_stem(f"{path.stem}_temp")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with temp_path.open("w", encoding=encoding) as outfile:
                json.dump(
                    payload, outfile, sort_keys=True, indent=4, ensure_ascii=False
                )
            temp_path.replace(path)
            return
        except OSError:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.1 * (attempt + 1))
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def clear_json(
    filename: str | PathLike[str],
    default: str = "{}",
    backup_amount: int = 3,
    backup_dir: Path | None = None,
    encoding: str = ENCODING,
) -> None:
    """Replace an existing JSON file with caller-provided JSON text.

    A missing file is left untouched. Existing files are backed up first when
    requested. ``default`` must contain valid JSON, but may represent any JSON
    type because this low-level helper writes the text verbatim.

    Raises:
        json.JSONDecodeError: If ``default`` is not valid JSON.
        OSError: If backup creation or the final write fails.
    """
    path = Path(filename)

    if not path.exists():
        return

    if backup_amount > 0:
        _create_backup(path, max_backups=backup_amount, backup_dir=backup_dir)

    # Reject invalid replacement text before overwriting the source file.
    json.loads(default)

    with path.open("w", encoding=encoding) as outfile:
        outfile.write(default)
