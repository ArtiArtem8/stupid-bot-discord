"""Validate the existing birthday JSON format before touching the pilot database."""

import json
from pathlib import Path
from typing import cast

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from utils.json_types import JsonObject, JsonValue, is_json_object


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key in birthday import")
        result[key] = value
    return result


def _identifier(value: JsonValue) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("Birthday IDs must be decimal strings")
    result = int(value)
    if result > 2**63 - 1:
        raise ValueError("Birthday ID exceeds SQLite's signed integer range")
    return result


def _string(data: JsonObject, field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Birthday field {field} must be a string")
    return value


def _user(uid: str, raw: JsonValue) -> BirthdayUser:
    if not isinstance(raw, dict):
        raise ValueError("Birthday member must be an object")
    history = raw.get("was_congrats", [])
    if not isinstance(history, list):
        raise ValueError("Congratulation history must be a list of strings")
    dates: list[str] = []
    for value in history:
        if not isinstance(value, str):
            raise ValueError("Congratulation history must contain strings")
        dates.append(value)
    return BirthdayUser(
        _identifier(uid), _string(raw, "name"), _string(raw, "birthday"), dates
    )


def _guild(gid: str, raw: JsonValue) -> BirthdayGuildConfig:
    if not isinstance(raw, dict) or not isinstance(raw.get("Users"), dict):
        raise ValueError("Birthday guild must have a Users object")
    role = raw.get("Birthday_role")
    result = BirthdayGuildConfig(
        _identifier(gid),
        _string(raw, "Server_name"),
        _identifier(raw.get("Channel_id")),
        birthday_role_id=None if role in (None, "") else _identifier(role),
    )
    members = raw["Users"]
    if not isinstance(members, dict):
        raise ValueError("Birthday Users must be an object")
    for uid, member in members.items():
        user = _user(uid, member)
        if user.user_id in result.users:
            raise ValueError("Duplicate normalized birthday member ID")
        result.users[user.user_id] = user
    return result


def load_birthdays(path: Path) -> list[BirthdayGuildConfig]:
    """Read and validate all guilds, preserving date strings and history order.

    Reject malformed records instead of silently dropping them during migration.
    Date interpretation remains the domain model's responsibility. No source
    bytes are changed. Call outside the event loop.
    """
    # JSON's untyped result is narrowed to object before validating its shape.
    raw = cast(
        object,
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object),
    )
    if not is_json_object(raw):
        raise ValueError("Birthday JSON must be an object")
    configs: dict[int, BirthdayGuildConfig] = {}
    for gid, value in raw.items():
        guild = _guild(gid, value)
        if guild.guild_id in configs:
            raise ValueError("Duplicate normalized birthday guild ID")
        configs[guild.guild_id] = guild
    return list(configs.values())
