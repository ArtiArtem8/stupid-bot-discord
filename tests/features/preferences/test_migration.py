"""Upgrade a prepared 0003 database without altering existing feature data."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from stupidbot.common.asyncio import run_in_thread
from stupidbot.features.music.repository import VolumeData, VolumeRepository
from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.storage.database import Database, migrate, open_engine, validate_schema


class PreferencesMigrationTests(IsolatedAsyncioTestCase):
    async def test_upgrade_preserves_volume_and_adds_no_implicit_overrides(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite"
            await run_in_thread(lambda: migrate(path, "0003_application"))
            old = Database(open_engine(path))
            try:
                await VolumeRepository(old).save(VolumeData(10, 42))
                with self.assertRaisesRegex(RuntimeError, "Unsupported SQLite schema"):
                    await validate_schema(old)
            finally:
                await old.close()
            await run_in_thread(lambda: migrate(path))
            upgraded = Database(open_engine(path))
            try:
                await validate_schema(upgraded)
                self.assertEqual(await VolumeRepository(upgraded).get_volume(10), 42)
                self.assertEqual(
                    await PreferencesRepository(upgraded).get(
                        PreferenceScope.GUILD, 10
                    ),
                    Preferences(),
                )
            finally:
                await upgraded.close()
