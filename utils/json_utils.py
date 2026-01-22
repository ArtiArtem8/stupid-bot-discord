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
    """Generates a name for a backup file, with following format:
    `<filename_stem>_<timestamp><random_suffix><extension>`.
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
    """Creates a backup for the file, *that must exist!*."""
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


def get_json(
    filename: str | PathLike[str], encoding: str = ENCODING
) -> JsonObject | None:
    """Reads a JSON file and returns its content as a dictionary.

    If the file does not exist or the content is not a valid JSON, returns None.
    """
    path = Path(filename)
    if not path.exists():
        return None
    try:
        with open(path, encoding=encoding) as data_file:
            payload: object = json.load(fp=data_file)  # pyright: ignore[reportAny]
            return payload if is_json_object(payload) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_json(
    filename: str | PathLike[str],
    data: JsonEncodableObject,
    backup_amount: int = 3,
    backup_dir: Path | None = None,
    encoding: str = ENCODING,
) -> None:
    """Saves a JSON file with the given data.

    Creates a backup of the file if it already exists and backup_amount > 0.
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
            with open(temp_path, "w", encoding=encoding) as outfile:
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
    """Clears the content of a JSON file and replaces it with a default value.

    If the file does not exist, no action is taken. Optionally creates backups
    of the file before clearing, keeping the specified number of backups.
    """
    path = Path(filename)

    if not path.exists():
        return

    if backup_amount > 0:
        _create_backup(path, max_backups=backup_amount, backup_dir=backup_dir)

    # Validate default is valid JSON
    json.loads(default)

    with open(path, "w", encoding=encoding) as outfile:
        outfile.write(default)
