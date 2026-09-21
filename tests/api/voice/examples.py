"""Small hand-computable voice histories shared by behavioral tests."""

from datetime import UTC, datetime, timedelta

from api.voice.model import (
    VoiceCheckpoint,
    VoiceFact,
    VoiceJournalRecord,
    VoiceObservation,
    VoiceSnapshot,
    VoiceStateSnapshot,
)
from api.voice.timeline import VoiceTimeline, build_timeline

START = datetime(2026, 9, 21, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def human(user_id: int = 1, channel_id: int | None = 10) -> VoiceStateSnapshot:
    return VoiceStateSnapshot(user_id, channel_id, is_bot=False)


def record(
    seconds: float,
    fact: VoiceFact,
    *,
    guild: int | None = 1,
    boot: str = "one",
    sequence: int | None = None,
) -> VoiceJournalRecord:
    return VoiceJournalRecord(
        int(seconds * 10) if sequence is None else sequence,
        boot,
        at(seconds),
        seconds,
        fact,
        guild,
    )


def example() -> VoiceTimeline:
    # 0-5 A; 5-10 AB; 10-15 ABC; 15-20 AC (minutes).
    return build_timeline(
        [
            record(0, VoiceSnapshot((human(),))),
            record(300, VoiceObservation(human(2))),
            record(600, VoiceObservation(human(3))),
            record(900, VoiceObservation(human(2, None))),
            record(1200, VoiceCheckpoint()),
        ]
    )
