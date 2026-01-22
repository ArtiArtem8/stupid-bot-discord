from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast

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
    """Convert a JSON-encodable value into frozen JSON primitives/containers."""
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
    """Convert a JSON-encodable mapping into a JSON object."""
    out_val = freeze_json(value)
    if not isinstance(out_val, dict):
        raise TypeError("Top-level JSON must be an object (mapping)")
    return out_val
