"""Birthday mutations, stale confirmations and conservative delivery claims."""

import asyncio
import unittest
from datetime import date
from typing import override

from sqlalchemy import select

from api.birthday_models import BirthdayDelivery
from repositories.birthday_sqlite.repository import SQLiteBirthdayRepository
from repositories.sqlite.identity import ensure_channel
from repositories.sqlite.schema import (
    birthday_deliveries,
    birthday_settings,
    music_settings,
)
from repositories.sqlite_volume_repository import SQLiteVolumeRepository
from repositories.volume_repository import VolumeData
from tests.storage import temporary_database


class TestSQLiteBirthday(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.path, self.database = await temporary_database(self)
        self.repo = SQLiteBirthdayRepository(self.database)

    async def test_mutations_preserve_settings_history_and_detached_reads(self) -> None:
        self.assertIsNone(await self.repo.get(1))
        self.assertEqual(
            await self.repo.clear_user_birthday(1, 2, expected_version=1),
            (False, False),
        )
        await self.repo.configure_guild(1, "Original", 10, 30)
        await self.repo.set_user_birthday(1, "Ignored", 99, 2, "Member", "01-01-2000")
        config = await self.repo.get(1)
        if config is None:
            self.fail("Missing guild")
        self.assertEqual(
            (config.server_name, config.channel_id, config.birthday_role_id),
            ("Original", 10, 30),
        )
        config.users.clear()
        loaded = await self.repo.get(1)
        if loaded is None:
            self.fail("Missing guild")
        self.assertIn(2, loaded.users)
        await self.repo.set_user_birthday(1, "Ignored", 99, 2, "Member", "02-01-2000")
        self.assertEqual(
            await self.repo.clear_user_birthday(1, 2, expected_version=1), (True, False)
        )
        self.assertEqual(
            await self.repo.clear_user_birthday(1, 2, expected_version=2), (True, True)
        )
        self.assertEqual(
            await self.repo.clear_user_birthday(1, 2, expected_version=2), (True, False)
        )

    async def test_concurrent_members_and_settings_have_no_lost_updates(self) -> None:
        await self.repo.configure_guild(1, "Guild", 10, None)
        await asyncio.gather(
            self.repo.configure_guild(1, "Changed", 77, 88),
            *(
                self.repo.set_user_birthday(
                    1, "Ignored", 99, uid, "Member", "01-01-2000"
                )
                for uid in range(1, 7)
            ),
        )
        loaded = await self.repo.get(1)
        if loaded is None:
            self.fail("Missing guild")
        self.assertEqual((loaded.channel_id, loaded.birthday_role_id), (77, 88))
        self.assertEqual(set(loaded.users), set(range(1, 7)))
        await SQLiteVolumeRepository(self.database).save(VolumeData(1, 50))
        self.assertTrue(await self.repo.delete(1))
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(music_settings.c.volume)), 50
            )

    async def test_only_one_claim_and_uncertain_send_is_not_repeated(self) -> None:
        await self.repo.set_user_birthday(1, "Guild", 10, 2, "Member", "06-10-2000")
        claims = [
            BirthdayDelivery(str(index), 1, 2, date(2026, 10, 6), 1, 1)
            for index in range(8)
        ]
        results = await asyncio.gather(
            *(self.repo.claim_delivery(claim) for claim in claims)
        )
        self.assertEqual(sum(results), 1)
        winner = claims[results.index(True)]
        self.assertTrue(await self.repo.begin_delivery(winner))
        self.assertFalse(await self.repo.begin_delivery(winner))
        self.assertFalse(await self.repo.claim_delivery(winner))
        await self.repo.finish_delivery(winner, 100)
        await self.repo.finish_delivery(winner, 100)
        loaded = await self.repo.get(1)
        if loaded is None:
            self.fail("Missing guild")
        self.assertEqual(loaded.users[2].was_congrats, ["06-10-2026"])

    async def test_changed_settings_or_birthday_prevents_claimed_send(self) -> None:
        await self.repo.set_user_birthday(1, "Guild", 10, 2, "Member", "06-10-2000")
        claim = BirthdayDelivery("stale", 1, 2, date(2026, 10, 6), 1, 1)
        self.assertTrue(await self.repo.claim_delivery(claim))
        await self.repo.configure_guild(1, "Guild", 20, None)
        self.assertFalse(await self.repo.begin_delivery(claim))
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(birthday_deliveries.c.status)),
                "obsolete",
            )

    async def test_cancelled_transaction_cannot_commit_other_task(self) -> None:
        await self.repo.configure_guild(1, "Original", 20, None)
        entered, second_started = asyncio.Event(), asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with self.database.transaction() as connection:
                await ensure_channel(connection, 999, 1)
                await connection.execute(
                    birthday_settings.update()
                    .where(birthday_settings.c.guild_id == 1)
                    .values(channel_id=999)
                )
                entered.set()
                await release.wait()

        async def second() -> None:
            second_started.set()
            await self.repo.configure_guild(2, "Independent", 30, None)

        first_task = asyncio.create_task(first())
        await entered.wait()
        second_task = asyncio.create_task(second())
        await second_started.wait()
        self.assertFalse(second_task.done())
        first_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_task
        await second_task
        self.assertEqual(
            [(g.guild_id, g.channel_id) for g in await self.repo.get_all()],
            [(1, 20), (2, 30)],
        )

    async def test_cancelled_pool_waiter_never_writes(self) -> None:
        started = asyncio.Event()
        async with self.database.transaction():

            async def waiting() -> None:
                started.set()
                await self.repo.configure_guild(1, "Never committed", 10, None)

            task = asyncio.create_task(waiting())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self.repo.get_all(), [])
        await self.repo.configure_guild(2, "Still usable", 20, None)
        self.assertEqual(await self.repo.get_all_guild_ids(), [2])
