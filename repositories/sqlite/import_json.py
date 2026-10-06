"""Validate migration inputs before writing either repository."""

import json
from pathlib import Path
from typing import cast

from repositories.volume_repository import VolumeData
from utils.json_types import JsonObject, JsonValue, is_json_object


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key in import")
        result[key] = value
    return result


def load_object(path: Path) -> JsonObject:
    """Read strict JSON without losing duplicate keys; call outside the event loop."""
    # Narrow the standard decoder's Any before checking the complete JSON shape.
    raw = cast(
        object,
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object),
    )
    if not is_json_object(raw):
        raise ValueError("Import JSON must be an object")
    return raw


def identifier(value: JsonValue) -> int:
    """Parse a decimal string ID fitting SQLite's signed integer range."""
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("IDs must be decimal strings")
    result = int(value)
    if result > 2**63 - 1:
        raise ValueError("ID exceeds SQLite's signed integer range")
    return result


def _volume(value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise ValueError("Volume must be an integer or an integer representation")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Import requires a finite integral volume")
    result = int(value)
    if not -(2**63) <= result < 2**63:
        raise ValueError("Volume exceeds SQLite's signed integer range")
    return result


def load_volumes(path: Path) -> list[VolumeData]:
    """Validate all settings before import, leaving source bytes untouched.

    Integer strings and integral floats normalize to the value the JSON reader
    would return. Reject booleans, fractional values and duplicate normalized IDs.
    Call outside the event loop. Invalid input propagates without partial import.
    """
    entries: dict[int, VolumeData] = {}
    for key, value in load_object(path).items():
        guild_id = identifier(key)
        if guild_id in entries:
            raise ValueError("Duplicate normalized volume guild ID")
        entries[guild_id] = VolumeData(guild_id, _volume(value))
    return list(entries.values())
