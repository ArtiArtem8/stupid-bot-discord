from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path
from typing import TypeGuard, cast

from stupidbot.settings import ENCODING

type JsonPrimitive = str | int | float | bool | None
type JsonArray = list["JsonValue"]
type JsonObject = dict[str, "JsonValue"]
type JsonValue = JsonPrimitive | JsonArray | JsonObject

type JsonEncodable = (
    JsonPrimitive | Sequence["JsonEncodable"] | Mapping[str, "JsonEncodable"]
)
type JsonEncodableObject = Mapping[str, JsonEncodable]


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Return True when value is JSON-serializable with primitive/list/object shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        items = cast(list[object], value)
        return all(is_json_value(item) for item in items)
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        return all(isinstance(k, str) and is_json_value(v) for k, v in items.items())
    return False


def is_json_object(value: object) -> TypeGuard[JsonObject]:
    """Return True when value is a JSON object with string keys."""
    if not isinstance(value, dict):
        return False
    items = cast(dict[object, object], value)
    return all(isinstance(k, str) and is_json_value(v) for k, v in items.items())


def freeze_json(value: JsonEncodable) -> JsonValue:
    """Copy a JSON-encodable value into mutable built-in JSON containers.

    Mappings become ``dict`` instances and non-string keys are rejected. Other
    sequences become lists; bytes and bytearrays are rejected rather than being
    treated as integer sequences.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        items = cast(Mapping[object, object], value)
        out: JsonObject = {}
        for k, v in items.items():
            if not isinstance(k, str):
                raise TypeError("JSON object keys must be str")
            out[k] = freeze_json(cast(JsonEncodable, v))
        return out

    if isinstance(value, (bytes, bytearray)):
        raise TypeError(f"Value is not JSON-encodable: {type(value)!r}")
    seq = cast(Sequence[object], value)
    return [freeze_json(cast(JsonEncodable, x)) for x in seq]


def freeze_json_object(value: JsonEncodableObject) -> JsonObject:
    """Copy a JSON-encodable mapping into an independent JSON object."""
    out_val = freeze_json(value)
    if not isinstance(out_val, dict):
        raise TypeError("Top-level JSON must be an object (mapping)")
    return out_val


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
