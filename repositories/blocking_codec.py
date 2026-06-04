"""Validation and decoding for the blocking repository JSON format."""

from collections.abc import Callable, Iterable, Mapping
from typing import TypeGuard, cast

from api.blocking_models import (
    BlockedUser,
    BlockedUserDict,
    BlockHistoryEntryDict,
    NameHistoryEntryDict,
)
from utils.json_types import JsonObject


def _as_str_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast(Mapping[str, object], mapping)


def as_json_object(value: object) -> JsonObject | None:
    """Return a string-keyed JSON object when the value has the expected shape."""
    if not isinstance(value, dict):
        return None
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast(JsonObject, mapping)


def _as_object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _has_string_fields(data: Mapping[str, object], fields: Iterable[str]) -> bool:
    return all(isinstance(data.get(field), str) for field in fields)


def _has_optional_string_field(data: Mapping[str, object], field: str) -> bool:
    value = data.get(field)
    return value is None or isinstance(value, str)


def _validated_list(
    data: Mapping[str, object],
    field: str,
    item_guard: Callable[[object], bool],
) -> list[object] | None:
    items = _as_object_list(data.get(field))
    if items is None or not all(item_guard(item) for item in items):
        return None
    return items


def _is_block_history_entry_dict(value: object) -> TypeGuard[BlockHistoryEntryDict]:
    data = _as_str_mapping(value)
    return data is not None and _has_string_fields(
        data, ("admin_id", "reason", "timestamp")
    )


def _is_name_history_entry_dict(value: object) -> TypeGuard[NameHistoryEntryDict]:
    data = _as_str_mapping(value)
    return data is not None and _has_string_fields(data, ("username", "timestamp"))


def _is_blocked_user_dict(value: object) -> TypeGuard[BlockedUserDict]:
    data = _as_str_mapping(value)
    if data is None:
        return False
    histories_are_valid = all(
        history is not None
        for history in (
            _validated_list(data, "block_history", _is_block_history_entry_dict),
            _validated_list(data, "unblock_history", _is_block_history_entry_dict),
            _validated_list(data, "name_history", _is_name_history_entry_dict),
        )
    )
    return (
        _has_string_fields(data, ("user_id", "current_username"))
        and _has_optional_string_field(data, "current_global_name")
        and isinstance(data.get("blocked"), bool)
        and histories_are_valid
    )


def try_decode_user(value: object) -> BlockedUser | None:
    """Decode a valid blocked-user JSON object, otherwise return None."""
    if not _is_blocked_user_dict(value):
        return None
    return BlockedUser.from_dict(value)
