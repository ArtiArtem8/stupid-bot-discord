import json
from pathlib import Path
import shutil
from config import ENCODING, BACKUP_DIR
import datetime


def _create_backup(filename: str | Path) -> None:
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir()
    filename = Path(filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{filename.stem}_{timestamp}{filename.suffix}"
    shutil.copy(filename, BACKUP_DIR / backup_filename)


def get_json(filename: str | Path) -> dict:
    with open(filename, encoding=ENCODING) as data_file:
        return json.load(data_file)


def save_json(filename: str | Path, data: dict) -> None:
    with open(filename, "w", encoding=ENCODING) as outfile:
        json.dump(data, outfile, sort_keys=True, indent=4, ensure_ascii=False)


def clear_json(filename: str | Path) -> None:
    _create_backup(filename)

    with open(filename, "w", encoding=ENCODING) as outfile:
        outfile.write("{}")
