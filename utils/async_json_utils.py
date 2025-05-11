# type: ignore

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from random import choices
from string import ascii_letters, digits
from typing import Optional

import aiofiles

from config import BACKUP_DIR, ENCODING


def _generate_backup_filename(filename: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = "".join(choices(ascii_letters + digits, k=4))
    return f"{filename.stem}_{timestamp}{rand_suffix}{filename.suffix}"


async def _create_backup_async(filename: Path, max_backups: int = 3) -> None:
    if not await asyncio.to_thread(BACKUP_DIR.exists):
        await asyncio.to_thread(BACKUP_DIR.mkdir, parents=True, exist_ok=True)

    backup_filename = _generate_backup_filename(filename)

    backups = await asyncio.to_thread(
        lambda: list(BACKUP_DIR.glob(f"{filename.stem}_*{filename.suffix}"))
    )

    backup_stats = await asyncio.gather(*[asyncio.to_thread(b.stat) for b in backups])
    sorted_backups = sorted(
        zip(backups, backup_stats), key=lambda x: x[1].st_mtime, reverse=True
    )

    for backup, _ in sorted_backups[max_backups:]:
        await asyncio.to_thread(backup.unlink)

    # Copy using thread pool (shutil is blocking)
    await asyncio.to_thread(shutil.copy, filename, BACKUP_DIR / backup_filename)


async def get_json_async(filename: str | Path) -> Optional[dict]:
    """Async read JSON with validation"""
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
    filename: str | Path, data: dict, backup_amount: int = 3
) -> None:
    """Atomic async JSON save with backups"""
    path = Path(filename)

    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

    if await asyncio.to_thread(path.exists) and backup_amount > 0:
        await _create_backup_async(path, backup_amount)

    temp_path = path.with_stem(f"{path.stem}_temp")
    json_data = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)

    async with aiofiles.open(temp_path, "w", encoding=ENCODING) as f:
        await f.write(json_data)

    await asyncio.to_thread(temp_path.replace, path)


async def clear_json_async(
    filename: str | Path, default: str = "{}", backup_amount: int = 3
) -> None:
    """Async clear JSON with validation"""
    path = Path(filename)

    if not await asyncio.to_thread(path.exists):
        return

    try:
        json.loads(default)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid default JSON") from e

    if backup_amount > 0:
        await _create_backup_async(path, backup_amount)

    async with aiofiles.open(path, "w", encoding=ENCODING) as f:
        await f.write(default)
