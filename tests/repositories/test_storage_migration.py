"""Exercise the complete one-shot migration on synthetic, immutable Windows copies."""

import gzip
import json
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import select

from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from repositories.sqlite.database import Database, open_engine, validate_schema
from repositories.sqlite.schema import reports, uptime_periods, users
from repositories.voice_repository import VoiceRepository
from tests.api.voice.examples import human, record
from tests.repositories.legacy_voice import encode_record
from tools import migrate_storage_once
from tools.migrate_storage_once import Arguments, migrate_snapshot
from tools.storage_legacy.import_features import report_time
from tools.storage_legacy.sources import LegacyData, read_sources
from tools.storage_legacy.verify import verify_import
from utils.json_types import JsonObject


class TestStorageMigration(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.args = Arguments()
        self.args.source = self.source
        self.args.destination = self.root / "application.sqlite"

    def write(self, name: str, value: JsonObject) -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    async def test_every_store_imports_and_uptime_retains_only_known_checkpoint(
        self,
    ) -> None:
        self.write(
            "user_birthdays.json",
            {
                "1": {
                    "Server_name": "Old guild",
                    "Channel_id": "10",
                    "Birthday_role": "30",
                    "Users": {
                        "2": {
                            "name": "Hint",
                            "birthday": "01-01-2000",
                            "was_congrats": ["x", "x"],
                        },
                        "1": {"name": "Other", "birthday": "", "was_congrats": []},
                    },
                }
            },
        )
        self.write("music_volumes.json", {"1": 0, "2": 180})
        self.write(
            "blocked_users.json",
            {
                "1": {
                    "users": {
                        "2": {
                            "user_id": "2",
                            "current_username": "Nickname",
                            "current_global_name": "old-name",
                            "blocked": True,
                            "block_history": [
                                {
                                    "admin_id": "3",
                                    "reason": "reason",
                                    "timestamp": "2026-10-06T00:00:00+00:00",
                                }
                            ],
                            "unblock_history": [],
                            "name_history": [],
                        }
                    }
                }
            },
        )
        self.write(
            "user_reports.json",
            {
                "report_channel_id": 99,
                "reports": [
                    {
                        "report_id": "legacy",
                        "reason": "Bug",
                        "created_at": "06.10.2026 00:10:00",
                        "user": {"id": 2, "name": "Historical author", "avatar": None},
                        "guild": {"id": None, "name": None},
                        "channel": {"id": 100, "name": "DM"},
                    }
                ],
            },
        )
        self.write("user_answers.json", {"2": {"already normalized?": "yes"}})
        self.write(
            "guild_monitor/guild_1.json",
            {
                "enabled": True,
                "ttl_days": None,
                "members": {
                    "2": {
                        "username": "Former member",
                        "roles": [30, 30],
                        "left_at": "2026-10-06T00:00:00+00:00",
                    }
                },
            },
        )
        self.write(
            "last_run.json", {"last_shutdown": 1000.5, "accumulated_uptime": 50.25}
        )
        voice = self.source / "voice_probe/v2/guild_1/events_2026-09-21.jsonl"
        voice.parent.mkdir(parents=True)
        voice.write_text(
            "\n".join(
                encode_record(item)
                for item in [
                    record(0, VoiceSnapshot((human(),))),
                    record(60, VoiceCheckpoint()),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        before = {
            path: sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        result = await migrate_snapshot(self.args)
        self.assertTrue(result["verified"])
        database = Database(open_engine(self.args.destination))
        try:
            await validate_schema(database)
            async with database.transaction() as connection:
                period = (
                    await connection.execute(
                        select(
                            uptime_periods.c.started_us,
                            uptime_periods.c.accumulated_us,
                            uptime_periods.c.last_checkpoint_us,
                            uptime_periods.c.archived_us,
                        )
                    )
                ).one()
                self.assertEqual(period, (None, 50_250_000, 1_000_500_000, None))
                self.assertIsNone(
                    await connection.scalar(
                        select(users.c.username).where(users.c.user_id == 2)
                    )
                )
                self.assertIsNone(await connection.scalar(select(reports.c.created_us)))
        finally:
            await database.close()
        self.assertEqual(
            before, {path: sha256(path.read_bytes()).hexdigest() for path in before}
        )
        with self.assertRaises(FileExistsError):
            await migrate_snapshot(self.args)

    async def test_duplicate_json_keys_refuse_before_creating_database(self) -> None:
        (self.source / "music_volumes.json").write_text(
            '{"1": 20, "1": 30}', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            await migrate_snapshot(self.args)
        self.assertFalse(self.args.destination.exists())
        self.assertFalse(list(self.root.glob("*.sqlite")))

    def test_legacy_dst_ambiguity_stays_unknown(self) -> None:
        timezone = ZoneInfo("America/New_York")
        self.assertEqual(
            report_time("01.11.2026 01:30:00", timezone),
            (None, "legacy_timezone_ambiguous"),
        )
        self.assertEqual(report_time("08.03.2026 02:30:00", timezone)[0], None)
        self.assertIsNotNone(report_time("06.10.2026 12:00:00", timezone)[0])

    async def test_failed_verification_leaves_unpublished_unusable_building_file(
        self,
    ) -> None:
        with patch(
            "tools.migrate_storage_once.verify_import",
            side_effect=ValueError("verification failed"),
        ):
            with self.assertRaisesRegex(ValueError, "verification failed"):
                await migrate_snapshot(self.args)
        self.assertFalse(self.args.destination.exists())
        building = self.args.destination.with_name(
            self.args.destination.name + ".building.sqlite"
        )
        database = Database(open_engine(building))
        try:
            with self.assertRaisesRegex(RuntimeError, "not been published"):
                await validate_schema(database)
        finally:
            await database.close()

    async def test_invalid_legacy_sources_are_rejected_before_creating_database(
        self,
    ) -> None:
        invalid = (
            ("music_volumes.json", '{"1":201}'),
            (
                "blocked_users.json",
                '{"1":{"users":{"2":{"user_id":"2","blocked":false,"block_history":[{}]}}}}',
            ),
            (
                "guild_monitor/guild_1.json",
                '{"enabled":true,"members":{"2":{"username":"u","roles":[0],"left_at":"2026-10-06T00:00:00Z"}}}',
            ),
            ("voice_probe/v2/guild_1/events_2026-10-06.jsonl", '{"version":999}'),
        )
        for name, content in invalid:
            with self.subTest(source=name):
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                try:
                    with self.assertRaises(ValueError):
                        await migrate_snapshot(self.args)
                    self.assertFalse(list(self.root.glob("*.sqlite")))
                finally:
                    path.unlink()

    async def test_prefix_opt_in_imports_full_representation_without_dedup(
        self,
    ) -> None:
        path = self.source / "voice_probe/v2/guild_1/events_2026-09-21.jsonl"
        path.parent.mkdir(parents=True)
        first = record(0, VoiceSnapshot((human(),)))
        last = record(60, VoiceCheckpoint())
        plain = (encode_record(first) + "\n").encode()
        full = plain + (encode_record(last) + "\n").encode() * 2
        path.write_bytes(plain)
        compressed = path.with_suffix(".jsonl.gz")
        compressed.write_bytes(gzip.compress(full))
        with self.assertRaisesRegex(ValueError, "Conflicting voice"):
            await migrate_snapshot(self.args)
        self.assertFalse(list(self.root.glob("*.sqlite")))
        self.args.allow_voice_prefix = True
        result = await migrate_snapshot(self.args)
        self.assertTrue(result["verified"])
        database = Database(open_engine(self.args.destination))
        try:
            await validate_schema(database)
            self.assertEqual(
                await VoiceRepository(database).read_all(1), (first, last, last)
            )
        finally:
            await database.close()
        self.assertEqual(path.read_bytes(), plain)
        self.assertEqual(gzip.decompress(compressed.read_bytes()), full)
        selected = read_sources(self.source, allow_voice_prefix=True)
        self.assertEqual(
            selected.voice_resolutions[path.relative_to(self.source).as_posix()],
            {
                "rule": "exact-ordered-prefix",
                "selected": compressed.relative_to(self.source).as_posix(),
                "prefix_records": 1,
                "selected_records": 3,
            },
        )

    def test_prefix_opt_in_still_rejects_divergence_torn_line_and_bad_schema(
        self,
    ) -> None:
        path = self.source / "voice_probe/v2/guild_1/events_2026-09-21.jsonl"
        path.parent.mkdir(parents=True)
        first = (encode_record(record(0, VoiceSnapshot(()))) + "\n").encode()
        second = (encode_record(record(1, VoiceCheckpoint())) + "\n").encode()
        for plain, full in (
            (first, second + first),
            (first[:-1], first + second),
            (first, first + b'{"version":999}\n'),
        ):
            with self.subTest(plain=plain, full=full):
                path.write_bytes(plain)
                path.with_suffix(".jsonl.gz").write_bytes(gzip.compress(full))
                with self.assertRaises(ValueError):
                    read_sources(self.source, allow_voice_prefix=True)

    async def test_source_change_after_verification_prevents_publication(self) -> None:
        self.write("music_volumes.json", {"1": 20})

        async def verified_then_changed(database: Database, data: LegacyData) -> None:
            await verify_import(database, data)
            self.write("music_volumes.json", {"1": 30})

        with (
            patch.object(
                migrate_storage_once, "verify_import", side_effect=verified_then_changed
            ),
            self.assertRaisesRegex(ValueError, "Source snapshot changed"),
        ):
            await migrate_snapshot(self.args)
        self.assertFalse(self.args.destination.exists())
        building = self.args.destination.with_name(
            self.args.destination.name + ".building.sqlite"
        )
        database = Database(open_engine(building))
        try:
            async with database.transaction() as connection:
                status = (
                    await connection.exec_driver_sql("SELECT status FROM storage_state")
                ).scalar_one()
            self.assertEqual(status, "BUILDING")
        finally:
            await database.close()
