# type: ignore

import asyncio
import json
import secrets
import shutil
from datetime import datetime
from os import stat_result
from pathlib import Path
from string import ascii_letters, digits
from typing import Any

import aiofiles

from config import BACKUP_DIR, ENCODING


def _generate_backup_filename(filename: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = "".join(secrets.choice(ascii_letters + digits) for _ in range(4))
    return f"{filename.stem}_{timestamp}{rand_suffix}{filename.suffix}"


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
            except (FileNotFoundError, OSError):
                continue

    existing_backups.sort(key=lambda x: x[1].st_mtime, reverse=True)

    for backup, _ in existing_backups[max_backups:]:
        try:
            await asyncio.to_thread(backup.unlink)
        except (FileNotFoundError, OSError):
            continue
    try:
        await asyncio.to_thread(shutil.copy, filename, BACKUP_DIR / backup_filename)
    except (FileNotFoundError, OSError) as e:
        print(f"Warning: Could not create backup: {e}")


async def get_json_async(filename: str | Path) -> dict[str, Any] | None:
    """Async read JSON with validation."""
    path = Path(filename)

    if not await asyncio.to_thread(path.exists):
        return None

    try:
        async with aiofiles.open(path, "r", encoding=ENCODING) as f:
            content = await f.read()
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


async def save_json_async(
    filename: str | Path, data: dict[str, Any], backup_amount: int = 3
) -> None:
    """Atomic async JSON save with backups."""
    if not isinstance(data, dict):
        raise TypeError("Data must be dict")
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
                break  # Success, exit the retry loop
            except (PermissionError, OSError):
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.001 * (attempt + 1))
    except Exception as e:
        try:
            await asyncio.to_thread(temp_path.unlink)
        except (FileNotFoundError, OSError):
            pass
        raise e


async def clear_json_async(
    filename: str | Path, default: str = "{}", backup_amount: int = 3
) -> None:
    """Async clear JSON with validation."""
    path = Path(filename)

    if not await asyncio.to_thread(path.exists):
        return

    try:
        json.loads(default)
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
    except (FileNotFoundError, OSError) as e:
        print(f"Warning: Could not clear file: {e}")
