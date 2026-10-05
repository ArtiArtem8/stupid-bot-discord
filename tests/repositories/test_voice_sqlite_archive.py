"""Keep archive selection equivalent to the existing journal's file preference."""

import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from api.voice.model import VoiceCheckpoint
from experiments.voice_sqlite.archive import read_archive
from repositories._voice_codec import encode_record
from tests.api.voice.examples import record


class TestVoiceArchive(unittest.TestCase):
    def test_scope_day_legacy_order_and_gzip_preference(self) -> None:
        legacy = record(1, VoiceCheckpoint())
        compressed = record(2, VoiceCheckpoint())
        ignored = record(3, VoiceCheckpoint())
        shared = record(4, VoiceCheckpoint(), guild=None)
        root = "project/data/voice_probe/"
        entries = (
            (
                root + "v2/session/events_2026-09-21.jsonl",
                encode_record(shared).encode(),
            ),
            (
                root + "v2/guild_1/events_2026-09-21.jsonl",
                encode_record(ignored).encode(),
            ),
            (root + "guild_1/events_2026-09-21.jsonl", encode_record(legacy).encode()),
            (
                root + "v2/guild_1/events_2026-09-21.jsonl.gz",
                gzip.compress(encode_record(compressed).encode()),
            ),
            ("project/data/other.json", b"not voice data"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.tar.xz"
            with tarfile.open(path, "w:xz") as archive:
                for name, data in entries:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            self.assertEqual(read_archive(path), (legacy, compressed, shared))
            self.assertEqual(
                [p.name for p in Path(directory).iterdir()], ["input.tar.xz"]
            )
