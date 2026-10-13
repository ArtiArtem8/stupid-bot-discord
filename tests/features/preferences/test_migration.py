"""Preserve feature data and settings across explicit presentation migrations."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from stupidbot.common.asyncio import run_in_thread
from stupidbot.features.music.repository import VolumeData, VolumeRepository
from stupidbot.features.preferences.models import (
    PreferenceConflictError,
    Preferences,
    PreferenceScope,
)
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import Language
from stupidbot.storage.database import Database, migrate, open_engine, validate_schema
from stupidbot.storage.schema import guild_preferences, user_preferences


class PreferencesMigrationTests(IsolatedAsyncioTestCase):
    async def test_korean_upgrade_preserves_overrides_revisions_and_reminders(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite"
            await run_in_thread(lambda: migrate(path, "0004_preferences"))
            old = Database(open_engine(path))
            now = datetime(2026, 10, 13, tzinfo=UTC)
            try:
                repository = PreferencesRepository(old)
                user = await repository.save(
                    PreferenceScope.USER, 10, Preferences(Language.EN, "Asia/Seoul")
                )
                guild = await repository.save(
                    PreferenceScope.GUILD, 20, Preferences(Language.RU, "UTC")
                )
                await repository.reserve_prompt_if_due(30, now)
                reservation = await repository.reserve_prompt_if_due(30, now)
                if reservation is None:
                    self.fail("Second use must reserve a reminder")
            finally:
                await old.close()
            await run_in_thread(lambda: migrate(path))
            upgraded = Database(open_engine(path))
            try:
                await validate_schema(upgraded)
                repository = PreferencesRepository(upgraded)
                self.assertEqual(await repository.get(PreferenceScope.USER, 10), user)
                self.assertEqual(await repository.get(PreferenceScope.GUILD, 20), guild)
                self.assertTrue(await repository.answer_prompt(reservation, never=True))
                for scope, subject_id, previous in (
                    (PreferenceScope.USER, 10, user),
                    (PreferenceScope.GUILD, 20, guild),
                ):
                    saved = await repository.save(
                        scope,
                        subject_id,
                        Preferences(Language.KO, previous.timezone, previous.revision),
                    )
                    self.assertEqual(saved.revision, previous.revision + 1)
                    self.assertEqual(await repository.get(scope, subject_id), saved)
                    with self.assertRaises(PreferenceConflictError):
                        await repository.save(scope, subject_id, previous)
                for table in (user_preferences, guild_preferences):
                    with self.assertRaises(IntegrityError):
                        async with upgraded.transaction() as connection:
                            await connection.execute(
                                table.update().values(language="unsupported")
                            )
                    with self.assertRaises(IntegrityError):
                        async with upgraded.transaction() as connection:
                            await connection.execute(table.update().values(revision=0))
                    with self.assertRaises(IntegrityError):
                        async with upgraded.transaction() as connection:
                            await connection.execute(
                                insert(table).values(
                                    subject_id=999, language="ko", revision=1
                                )
                            )
                async with upgraded.transaction() as connection:
                    violations = await connection.execute(
                        text("PRAGMA foreign_key_check")
                    )
                    self.assertEqual(violations.all(), [])
            finally:
                await upgraded.close()

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
