"""Voice journal wire schema and one-way legacy decoding; no file I/O."""

import json
from datetime import datetime, timedelta

from api.voice.model import (
    GapReason,
    ObservationGap,
    VoiceCheckpoint,
    VoiceFact,
    VoiceJournalRecord,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from utils.json_types import JsonObject, JsonValue, is_json_object


def encode_record(record: VoiceJournalRecord) -> str:
    """Encode only schema v2, with readable field names and explicit unknowns."""
    line: JsonObject = {
        "schema_version": 2,
        "sequence": record.sequence,
        "boot_id": record.boot_id,
        "observed_at": record.observed_at.isoformat(),
        "monotonic": record.monotonic,
        "guild_id": record.guild_id,
    }
    line.update(_encode_fact(record.fact))
    return json.dumps(line, ensure_ascii=False, allow_nan=False)


def _encode_state(state: VoiceStateSnapshot) -> JsonObject:
    return {
        "user_id": state.user_id,
        "channel_id": state.channel_id,
        "is_bot": state.is_bot,
        "self_mute": state.self_mute,
        "self_deaf": state.self_deaf,
        "server_mute": state.server_mute,
        "server_deaf": state.server_deaf,
        "self_stream": state.self_stream,
        "self_video": state.self_video,
        "suppress": state.suppress,
        "requested_to_speak": state.requested_to_speak,
        "requested_to_speak_at": (
            state.requested_to_speak_at.isoformat()
            if state.requested_to_speak_at
            else None
        ),
        "session_id": state.session_id,
        "channel_known": state.channel_known,
        "afk": state.afk,
    }


def _encode_fact(fact: VoiceFact) -> JsonObject:
    match fact:
        case VoiceObservation(state):
            return {"kind": "observation", "state": _encode_state(state)}
        case VoiceSnapshot(states, authoritative):
            return {
                "kind": "snapshot",
                "authoritative": authoritative,
                "states": [_encode_state(state) for state in states],
            }
        case ObservationGap(start, end, reason, _, known_bounds):
            return {
                "kind": "gap",
                "started_at": start.isoformat(),
                "ended_at": end.isoformat() if end else None,
                "reason": reason.value,
                "known_bounds": known_bounds,
            }
        case VoiceLifecycle(stopped):
            return {"kind": "lifecycle", "stopped": stopped}
        case VoiceCheckpoint():
            return {"kind": "checkpoint"}


def decode_record(line: str) -> VoiceJournalRecord:
    """Decode v2 or legacy facts; reject corruption instead of hiding lost time."""
    # json.loads has an Any return; validate once at this serialization boundary.
    value: object = json.loads(line)  # pyright: ignore[reportAny]
    raw = _object(value)
    if "schema_version" not in raw:
        return _decode_legacy(raw)
    if raw["schema_version"] != 2:
        raise ValueError("Unsupported voice journal schema")
    guild_id = _integer(raw.get("guild_id"))
    return VoiceJournalRecord(
        sequence=_required_integer(raw.get("sequence")),
        boot_id=_string(raw.get("boot_id")),
        observed_at=_datetime(raw.get("observed_at")),
        monotonic=_number(raw.get("monotonic")),
        guild_id=guild_id,
        fact=_decode_fact(raw, guild_id),
    )


def _decode_fact(raw: JsonObject, guild_id: int | None) -> VoiceFact:
    match raw.get("kind"):
        case "observation":
            return VoiceObservation(_decode_state(_object(raw.get("state"))))
        case "snapshot":
            values = raw.get("states")
            if not isinstance(values, list):
                raise ValueError("Snapshot states must be an array")
            authoritative = _boolean(raw.get("authoritative"))
            if authoritative is None:
                raise ValueError("Snapshot authority must be explicit")
            return VoiceSnapshot(
                tuple(_decode_state(_object(v)) for v in values), authoritative
            )
        case "gap":
            end = raw.get("ended_at")
            return ObservationGap(
                _datetime(raw.get("started_at")),
                _datetime(end) if end else None,
                GapReason(_string(raw.get("reason"))),
                guild_id,
                _boolean(raw.get("known_bounds")) is True,
            )
        case "lifecycle":
            return VoiceLifecycle(_boolean(raw.get("stopped")) is True)
        case "checkpoint":
            return VoiceCheckpoint()
        case _:
            raise ValueError("Unknown voice fact kind")


def _decode_state(raw: JsonObject) -> VoiceStateSnapshot:
    requested = raw.get("requested_to_speak_at")
    if "channel_id" not in raw:
        raise ValueError("A state must explicitly carry channel_id")
    return VoiceStateSnapshot(
        user_id=_required_integer(raw.get("user_id")),
        channel_id=_integer(raw.get("channel_id")),
        is_bot=_boolean(raw.get("is_bot")),
        self_mute=_boolean(raw.get("self_mute")),
        self_deaf=_boolean(raw.get("self_deaf")),
        server_mute=_boolean(raw.get("server_mute")),
        server_deaf=_boolean(raw.get("server_deaf")),
        self_stream=_boolean(raw.get("self_stream")),
        self_video=_boolean(raw.get("self_video")),
        suppress=_boolean(raw.get("suppress")),
        requested_to_speak=_boolean(raw.get("requested_to_speak")),
        requested_to_speak_at=_datetime(requested) if requested else None,
        session_id=_optional_string(raw.get("session_id")),
        channel_known=_boolean(raw.get("channel_known")) is not False,
        afk=_boolean(raw.get("afk")),
    )


def _decode_legacy(raw: JsonObject) -> VoiceJournalRecord:
    moment = _datetime(raw.get("at"))
    guild_id = _integer(raw.get("guild"))
    return VoiceJournalRecord(
        _required_integer(raw.get("seq")),
        _string(raw.get("boot")),
        moment,
        _number(raw.get("mono")),
        _legacy_fact(raw, guild_id, moment),
        guild_id,
    )


def _legacy_fact(raw: JsonObject, guild_id: int | None, moment: datetime) -> VoiceFact:
    kind = raw.get("kind")
    detail = _object(raw.get("detail") or {})
    if kind in {"join", "leave", "move", "flags", "noop", "bot_voice"}:
        return VoiceObservation(_legacy_state(raw, kind == "bot_voice"))
    if kind in {"startup", "heartbeat", "reconcile"}:
        return _legacy_snapshot(detail)
    if kind in {"disconnect", "resume", "overflow", "monotonic_jump"}:
        return _legacy_gap(str(kind), detail, guild_id, moment)
    if kind == "drift":
        return ObservationGap(
            moment, None, GapReason.UNKNOWN, guild_id, known_bounds=False
        )
    raise ValueError("Unknown legacy voice record")


def _legacy_state(raw: JsonObject, is_bot: bool) -> VoiceStateSnapshot:
    flags = _object(raw.get("flags_after") or {})
    return VoiceStateSnapshot(
        _required_integer(raw.get("user")),
        _integer(raw.get("channel_after")),
        is_bot,
        self_mute=_boolean(flags.get("sm")),
        self_deaf=_boolean(flags.get("sd")),
        server_mute=_boolean(flags.get("m")),
        server_deaf=_boolean(flags.get("d")),
        self_stream=_boolean(flags.get("st")),
        self_video=_boolean(flags.get("sv")),
        suppress=_boolean(flags.get("sp")),
        requested_to_speak=_boolean(flags.get("hr")),
    )


def _legacy_snapshot(detail: JsonObject) -> VoiceFact:
    if detail.get("guild_removed") is True:
        return VoiceLifecycle(stopped=True)
    if "presence" not in detail and "bot_presence" not in detail:
        return VoiceCheckpoint()
    states = _legacy_members(_object(detail.get("presence") or {}), is_bot=False)
    states.extend(
        _legacy_members(_object(detail.get("bot_presence") or {}), is_bot=True)
    )
    return VoiceSnapshot(
        tuple(states), authoritative="presence" in detail and "bot_presence" in detail
    )


def _legacy_members(channels: JsonObject, *, is_bot: bool) -> list[VoiceStateSnapshot]:
    states: list[VoiceStateSnapshot] = []
    for channel, users in channels.items():
        if not isinstance(users, list):
            raise ValueError("Invalid legacy presence map")
        for user in users:
            states.append(
                VoiceStateSnapshot(_required_integer(user), int(channel), is_bot)
            )
    return states


def _legacy_gap(
    kind: str, detail: JsonObject, guild_id: int | None, moment: datetime
) -> ObservationGap:
    reason = {
        "disconnect": GapReason.GATEWAY_DISCONNECT,
        "resume": GapReason.GATEWAY_DISCONNECT,
        "overflow": GapReason.WRITER_OVERFLOW,
        "monotonic_jump": GapReason.CLOCK_DISCONTINUITY,
    }[kind]
    seconds = detail.get("gap_seconds", detail.get("delta_seconds"))
    # Old overflow markers have no precise loss timestamp. Replay expands this
    # conservative bound to the start of the boot when necessary.
    start = (
        moment
        if seconds is None
        else moment - timedelta(seconds=max(0.0, _number(seconds)))
    )
    return ObservationGap(
        start, None, reason, guild_id, known_bounds=kind == "disconnect"
    )


def _object(value: object) -> JsonObject:
    if not is_json_object(value):
        raise ValueError("Expected a JSON object")
    return value


def _integer(value: JsonValue) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not an ID")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("Expected an integer")


def _required_integer(value: JsonValue) -> int:
    result = _integer(value)
    if result is None:
        raise ValueError("Missing integer")
    return result


def _boolean(value: JsonValue) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("Expected a boolean or unknown")


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _optional_string(value: JsonValue) -> str | None:
    return None if value is None else _string(value)


def _datetime(value: JsonValue) -> datetime:
    result = datetime.fromisoformat(_string(value))
    if result.utcoffset() is None:
        raise ValueError("Expected an aware timestamp")
    return result


def _number(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number")
    return float(value)
