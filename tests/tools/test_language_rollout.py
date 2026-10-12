"""Deployment seeding is explicit, atomic and never overwrites existing settings."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select

from stupidbot.features.preferences.models import Preferences, PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import Language
from stupidbot.storage.schema import guild_preferences, guilds
from tests.storage.fixtures import temporary_database
from tools.language_rollout import (
    GuildLanguage,
    read_reviewed_inventory,
    seed_languages,
)


class TestLanguageRollout(unittest.IsolatedAsyncioTestCase):
    async def test_preview_writes_nothing_and_apply_preserves_explicit_reset(
        self,
    ) -> None:
        _, database = await temporary_database(self)
        repository = PreferencesRepository(database)
        await repository.save(PreferenceScope.GUILD, 1, Preferences(Language.EN, "UTC"))
        await repository.save(
            PreferenceScope.GUILD, 2, Preferences(None, "Europe/Moscow")
        )
        entries = tuple(GuildLanguage(guild_id, Language.RU) for guild_id in (1, 2, 3))
        preview = await seed_languages(database, entries, apply=False)
        self.assertEqual((preview.inserted, preview.preserved), (1, 2))
        async with database.transaction() as connection:
            self.assertEqual(
                list(await connection.scalars(select(guilds.c.guild_id))), [1, 2]
            )
        applied = await seed_languages(database, entries, apply=True)
        self.assertEqual(applied, preview)
        self.assertEqual(
            await repository.get(PreferenceScope.GUILD, 1),
            Preferences(Language.EN, "UTC", 1),
        )
        self.assertEqual(
            await repository.get(PreferenceScope.GUILD, 2),
            Preferences(None, "Europe/Moscow", 1),
        )
        self.assertEqual(
            await repository.get(PreferenceScope.GUILD, 3),
            Preferences(Language.RU, None, 1),
        )
        again = await seed_languages(database, entries, apply=True)
        self.assertEqual((again.inserted, again.preserved), (0, 3))

    async def test_failed_batch_rolls_back_earlier_inserts(self) -> None:
        _, database = await temporary_database(self)
        entries = (GuildLanguage(1, Language.RU), GuildLanguage(-1, Language.RU))
        with self.assertRaises(ValueError):
            await seed_languages(database, entries, apply=True)
        async with database.transaction() as connection:
            self.assertEqual(
                list(await connection.scalars(select(guild_preferences.c.subject_id))),
                [],
            )

    def test_manifest_requires_complete_unique_reviewed_language_decisions(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            for rows, count in (
                ([{"guild_id": "1", "language": "ru", "reviewed": False}], 1),
                ([{"guild_id": "1", "language": "zz", "reviewed": True}], 1),
                ([{"guild_id": "1", "language": "en", "reviewed": True}] * 2, 2),
                ([{"guild_id": "1", "language": "en", "reviewed": True}], 2),
            ):
                with self.subTest(rows=rows, count=count):
                    path.write_text(
                        json.dumps({"format": 1, "guild_count": count, "guilds": rows}),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        read_reviewed_inventory(path)
            path.write_text(
                json.dumps(
                    {
                        "format": 1,
                        "guild_count": 1,
                        "guilds": [
                            {"guild_id": "1", "language": "en", "reviewed": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                read_reviewed_inventory(path), (GuildLanguage(1, Language.EN),)
            )
