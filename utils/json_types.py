from __future__ import annotations

from typing import TypeGuard, cast

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


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
