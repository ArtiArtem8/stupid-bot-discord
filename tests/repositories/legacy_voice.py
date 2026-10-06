"""Build legacy JSONL fixtures for decoder and complete-import tests."""

import json

from api.voice.model import (
    ObservationGap,
    VoiceCheckpoint,
    VoiceFact,
    VoiceJournalRecord,
    VoiceLifecycle,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from utils.json_types import JsonObject


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
