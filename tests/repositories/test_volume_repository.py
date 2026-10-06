"""Volume scope, normalization and forward upgrades preserve existing birthday data."""

import asyncio
import unittest
from functools import partial
from typing import override

from sqlalchemy import insert, select

import config
from repositories.birthday_repository import BirthdayRepository
from repositories.sqlite.database import (
    Database,
    copy_database,
    migrate,
    open_engine,
    validate_schema,
)
from repositories.sqlite.schema import music_settings
from repositories.volume_repository import VolumeData, VolumeRepository
from tests.storage import temporary_database
from tools.storage_legacy.partial_schema import congratulations, guilds, users, volumes
from utils.asyncio_utils import run_in_thread


class TestVolumeRepository(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.path, self.database = await temporary_database(self)
        self.repo = VolumeRepository(self.database)

    async def test_missing_default_zero_and_independent_guilds(self) -> None:
        self.assertIsNone(await self.repo.get(1))
        self.assertEqual(await self.repo.get_volume(1), config.MUSIC_DEFAULT_VOLUME)
        await asyncio.gather(
            self.repo.save(VolumeData(1, 0)), self.repo.save(VolumeData(2, 200))
        )
        await self.repo.save(VolumeData(1, 0))
        self.assertEqual(
            await self.repo.get_all(), [VolumeData(1, 0), VolumeData(2, 200)]
        )
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(
                    select(music_settings.c.version).where(
                        music_settings.c.guild_id == 1
                    )
                ),
                1,
            )
        with self.assertRaises(ValueError):
            await self.repo.save(VolumeData(1, 201))
        self.assertEqual(await self.repo.get_volume(1), 0)

    async def test_forward_upgrade_and_backup_keep_duplicate_birthday_history(
        self,
    ) -> None:
        for revision in ("0001_birthdays", "0002_music_volume"):
            with self.subTest(revision=revision):
                old_path = self.path.with_name(revision + ".sqlite")
                await run_in_thread(partial(migrate, old_path, revision))
                old = Database(open_engine(old_path))
                async with old.transaction() as connection:
                    await connection.execute(
                        insert(guilds).values(
                            guild_id=1,
                            server_name="Guild",
                            channel_id=10,
                            birthday_role_id=None,
                        )
                    )
                    await connection.execute(
                        insert(users).values(
                            guild_id=1,
                            user_id=2,
                            name="Member",
                            birthday="01-01-2000",
                            position=0,
                        )
                    )
                    for position in (0, 1):
                        await connection.execute(
                            insert(congratulations).values(
                                guild_id=1, user_id=2, position=position, value="old"
                            )
                        )
                    if revision == "0002_music_volume":
                        await connection.execute(
                            insert(volumes).values(guild_id=1, volume=33)
                        )
                await old.close()
                await run_in_thread(partial(migrate, old_path))
                upgraded = Database(open_engine(old_path))
                try:
                    await validate_schema(upgraded)
                    birthday = await BirthdayRepository(upgraded).get(1)
                    if birthday is None:
                        self.fail("Birthday lost during upgrade")
                    self.assertEqual(birthday.users[2].was_congrats, ["old", "old"])
                    volume = await VolumeRepository(upgraded).get_volume(1)
                    self.assertEqual(
                        volume,
                        33
                        if revision == "0002_music_volume"
                        else config.MUSIC_DEFAULT_VOLUME,
                    )
                    backup = old_path.with_suffix(".backup.sqlite")
                    await run_in_thread(partial(copy_database, old_path, backup))
                    restored = Database(open_engine(backup))
                    try:
                        await validate_schema(restored)
                        self.assertEqual(
                            await BirthdayRepository(restored).get(1), birthday
                        )
                    finally:
                        await restored.close()
                finally:
                    await upgraded.close()
