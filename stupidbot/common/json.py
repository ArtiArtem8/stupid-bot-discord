from __future__ import annotations

import json
from os import PathLike
from pathlib import Path
from typing import TypeGuard, cast

from stupidbot.settings import ENCODING

type JsonPrimitive = str | int | float | bool | None
type JsonArray = list["JsonValue"]
type JsonObject = dict[str, "JsonValue"]
type JsonValue = JsonPrimitive | JsonArray | JsonObject


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Return True when value is JSON-serializable with primitive/list/object shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        items = cast(list[object], value)
        return all(is_json_value(item) for item in items)
    return is_json_object(value)


def is_json_object(value: object) -> TypeGuard[JsonObject]:
    """Return True when value is a JSON object with string keys."""
    if not isinstance(value, dict):
        return False
    items = cast(dict[object, object], value)
    return all(isinstance(k, str) and is_json_value(v) for k, v in items.items())


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
