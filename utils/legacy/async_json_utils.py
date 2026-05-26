import asyncio
import json
import secrets
import shutil
from datetime import datetime
from os import stat_result
from pathlib import Path
from string import ascii_letters, digits
from typing import cast

import aiofiles

from config import BACKUP_DIR, ENCODING
from utils.json_types import JsonObject


def _generate_backup_filename(filename: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = "".join(secrets.choice(ascii_letters + digits) for _ in range(4))
    return f"{filename.stem}_{timestamp}{rand_suffix}{filename.suffix}"


def _is_json_value(value: object) -> bool:
    """Return True if value can be represented as JSON data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True

    if isinstance(value, list):
        items = cast(list[object], value)
        return all(_is_json_value(item) for item in items)

    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in mapping.items()
        )

    return False


def _decode_json_object(content: str) -> JsonObject | None:
    value = cast(object, json.loads(content))

    if not isinstance(value, dict):
        return None

    mapping = cast(dict[object, object], value)
    if not all(
        isinstance(key, str) and _is_json_value(item) for key, item in mapping.items()
    ):
        return None

    return cast(JsonObject, mapping)


def _validate_json_document(content: str) -> None:
    value = cast(object, json.loads(content))
    if not _is_json_value(value):
        raise ValueError("Default value is not valid JSON data")


async def _create_backup_async(filename: Path, max_backups: int = 3) -> None:
    if not await asyncio.to_thread(BACKUP_DIR.exists):
        await asyncio.to_thread(BACKUP_DIR.mkdir, parents=True, exist_ok=True)

    backup_filename = _generate_backup_filename(filename)

    backups = await asyncio.to_thread(
        lambda: list(BACKUP_DIR.glob(f"{filename.stem}_*{filename.suffix}"))
    )
    existing_backups: list[tuple[Path, stat_result]] = []
    for backup in backups:
        if await asyncio.to_thread(backup.exists):
            try:
                stat_res = await asyncio.to_thread(backup.stat)
                existing_backups.append((backup, stat_res))
            except OSError:
                continue

    existing_backups.sort(key=lambda item: item[1].st_mtime, reverse=True)

    for backup, _ in existing_backups[max_backups:]:
        try:
            await asyncio.to_thread(backup.unlink)
        except OSError:
            continue
    try:
        await asyncio.to_thread(shutil.copy, filename, BACKUP_DIR / backup_filename)
    except OSError as e:
        print(f"Warning: Could not create backup: {e}")


async def get_json_async(filename: str | Path) -> JsonObject | None:
    """Async read JSON object with runtime validation."""
    path = Path(filename)

    if not await asyncio.to_thread(path.exists):
        return None

    try:
        async with aiofiles.open(path, "r", encoding=ENCODING) as f:
            content = await f.read()
        return _decode_json_object(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


async def save_json_async(
    filename: str | Path, data: JsonObject, backup_amount: int = 3
) -> None:
    """Atomic async JSON object save with backups."""
    path = Path(filename)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

    try:
        if await asyncio.to_thread(path.exists) and backup_amount > 0:
            await _create_backup_async(path, backup_amount)
    except Exception as e:
        print(f"Warning: Backup creation failed: {e}")

    temp_path = path.with_name(_generate_backup_filename(path))
    temp_path = temp_path.with_stem(f"{temp_path.stem}_temp")
    json_data = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)

    try:
        async with aiofiles.open(temp_path, "w", encoding=ENCODING) as f:
            await f.write(json_data)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.to_thread(temp_path.replace, path)
                break
            except OSError:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.001 * (attempt + 1))
    except Exception as e:
        try:
            await asyncio.to_thread(temp_path.unlink)
        except OSError:
            pass
        raise e


async def clear_json_async(
    filename: str | Path, default: str = "{}", backup_amount: int = 3
) -> None:
    """Async clear JSON file with validation and optional backup."""
    path = Path(filename)

    if not await asyncio.to_thread(path.exists):
        return

    try:
        _validate_json_document(default)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid default JSON") from e

    try:
        if backup_amount > 0:
            await _create_backup_async(path, backup_amount)
    except Exception as e:
        print(f"Warning: Backup creation failed: {e}")

    try:
        async with aiofiles.open(path, "w", encoding=ENCODING) as f:
            await f.write(default)
    except OSError as e:
        print(f"Warning: Could not clear file: {e}")
