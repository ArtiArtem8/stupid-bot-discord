"""Read only archived voice facts, without extracting archive paths to disk."""

import gzip
import re
import tarfile
from pathlib import Path

from api.voice.model import VoiceJournalRecord
from repositories._voice_codec import decode_record

_PATH = re.compile(
    r"(?:.*/)?data/voice_probe/(?P<v2>v2/)?(?P<scope>session|guild_[0-9]+)/events_(?P<day>[0-9-]+)\.jsonl(?P<gzip>\.gz)?$"
)


def read_archive(path: Path) -> tuple[VoiceJournalRecord, ...]:
    """Read scope/day/legacy-v2 order, preferring gzip exactly like VoiceJournal.

    No non-journal members are opened and no files are extracted. The entire
    frozen input is loaded once outside the measured workload. Errors propagate.
    """
    with tarfile.open(path) as archive:
        selected: dict[tuple[str, str, bool], tarfile.TarInfo] = {}
        for member in archive.getmembers():
            match = _PATH.fullmatch(member.name)
            if not member.isfile() or match is None:
                continue
            key = (match["scope"], match["day"], bool(match["v2"]))
            if key not in selected or match["gzip"]:
                selected[key] = member
        records: list[VoiceJournalRecord] = []
        for key in sorted(selected):
            member = selected[key]
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("Missing archived voice member")
            with handle:
                payload = handle.read()
            if member.name.endswith(".gz"):
                payload = gzip.decompress(payload)
            records.extend(
                decode_record(line)
                for line in payload.decode("utf-8").splitlines()
                if line.strip()
            )
    if not records:
        raise ValueError("Archive contains no voice facts")
    return tuple(records)
