import json
import shutil
from datetime import datetime
from pathlib import Path
from random import choices
from string import ascii_letters, digits

from config import BACKUP_DIR, ENCODING


def _generate_backup_filename(filename: Path) -> str:
    """Generates a name for a backup file, with folowing format:
    `<filename>_<random_letters><timestamp>.<filename_extension>`"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = "".join(choices(ascii_letters + digits, k=4))  # 4 random characters
    return f"{filename.stem}_{timestamp}{rand_suffix}{filename.suffix}"


def _create_backup(filename: str | Path, max_backups: int = 3) -> None:
    """creates a backup for the file, *that must exist!*"""
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(filename)
    backup_filename = _generate_backup_filename(filename)
    backups = BACKUP_DIR.glob(f"{filename.stem}_*{filename.suffix}")
    # sorting by time
    sorted_backups = sorted(backups, reverse=True, key=lambda x: x.stat().st_mtime)
    for old in sorted_backups[max_backups:]:
        old.unlink()
    shutil.copy(filename, BACKUP_DIR / backup_filename)  # creating and writing file


def get_json(filename: str | Path) -> dict | None:
    """Reads a JSON file and returns its content as a dictionary.

    If the file does not exist or the content is not a valid JSON, returns None.
    """
    filename = Path(filename)
    if not filename.exists():
        return None
    try:
        with open(filename, encoding=ENCODING) as data_file:
            return json.load(data_file)
    except json.JSONDecodeError:
        return None


def save_json(filename: str | Path, data: dict, backup_amount: int = 3) -> None:
    """Saves a JSON file with the given data.

    Creates a backup of the file if it already exists and backup_amount > 0.
    """
    filename = Path(filename)

    filename.parent.mkdir(parents=True, exist_ok=True)  # creating parent directory

    if filename.exists() and backup_amount > 0:
        _create_backup(filename)
    temp_filename = filename.with_stem(f"{filename.stem}_temp")
    with open(temp_filename, "w", encoding=ENCODING) as outfile:
        json.dump(data, outfile, sort_keys=True, indent=4, ensure_ascii=False)

    temp_filename.replace(filename)


def clear_json(
    filename: str | Path, default: str = "{}", backup_amount: int = 3
) -> None:
    """
    Clears the content of a JSON file and replaces it with a default value.

    If the file does not exist, no action is taken. Optionally creates backups
    of the file before clearing, keeping the specified number of backups.

    Args:
        filename (str | Path): The path to the JSON file to be cleared.
        default (str): The default JSON content to write to the file. Must be a valid JSON string.
        backup_amount (int): The number of backups to keep. If greater than 0, creates backups.

    Returns:
        None
    """

    filename = Path(filename)

    if not filename.exists():
        return  # do nothing, clear nothing

    if backup_amount > 0:
        _create_backup(filename, max_backups=backup_amount)

    json.loads(default)  # validate

    with open(filename, "w", encoding=ENCODING) as outfile:
        outfile.write(default)
