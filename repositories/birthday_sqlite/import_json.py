"""Validate the existing birthday JSON format before touching the birthday database."""

from pathlib import Path

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from repositories.sqlite.import_json import identifier, load_object
from utils.json_types import JsonObject, JsonValue


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
        identifier(uid), _string(raw, "name"), _string(raw, "birthday"), dates
    )


def _guild(gid: str, raw: JsonValue) -> BirthdayGuildConfig:
    if not isinstance(raw, dict) or not isinstance(raw.get("Users"), dict):
        raise ValueError("Birthday guild must have a Users object")
    role = raw.get("Birthday_role")
    result = BirthdayGuildConfig(
        identifier(gid),
        _string(raw, "Server_name"),
        identifier(raw.get("Channel_id")),
        birthday_role_id=None if role in (None, "") else identifier(role),
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
    raw = load_object(path)
    configs: dict[int, BirthdayGuildConfig] = {}
    for gid, value in raw.items():
        guild = _guild(gid, value)
        if guild.guild_id in configs:
            raise ValueError("Duplicate normalized birthday guild ID")
        configs[guild.guild_id] = guild
    return list(configs.values())
