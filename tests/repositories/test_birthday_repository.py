"""Birthday mutations, stale confirmations and conservative delivery claims."""

import asyncio
import unittest
from datetime import date
from typing import override

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.birthday_models import BirthdayDelivery
from repositories.birthday_repository import BirthdayRepository
from repositories.sqlite.database import Database, open_engine
from repositories.sqlite.identity import ensure_channel
from repositories.sqlite.schema import (
    birthday_deliveries,
    birthday_settings,
    music_settings,
)
from repositories.volume_repository import VolumeData, VolumeRepository
from tests.storage import temporary_database


class TestBirthdayRepository(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.path, self.database = await temporary_database(self)
        self.repo = BirthdayRepository(self.database)

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
        await VolumeRepository(self.database).save(VolumeData(1, 50))
        await self.repo.clear_user_birthday(1, 1, expected_version=1)
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(music_settings.c.volume)), 50
            )

    async def test_restart_releases_only_claimed_and_preserves_delivery_states(
        self,
    ) -> None:
        claims: list[BirthdayDelivery] = []
        for uid in (2, 3, 4):
            await self.repo.set_user_birthday(
                1, "Guild", 10, uid, "Member", "06-10-2000"
            )
            claim = BirthdayDelivery(str(uid), 1, uid, date(2026, 10, 6), 1, 1)
            self.assertTrue(await self.repo.claim_delivery(claim))
            claims.append(claim)
        self.assertTrue(await self.repo.begin_delivery(claims[1]))
        self.assertTrue(await self.repo.begin_delivery(claims[2]))
        await self.repo.finish_delivery(claims[2], 100)
        await self.database.close()
        self.database = Database(open_engine(self.path))
        self.addAsyncCleanup(self.database.close)
        self.repo = BirthdayRepository(self.database)
        for _ in range(2):
            with self.assertLogs(
                "repositories.birthday_repository", level="WARNING"
            ) as logs:
                await self.repo.recover_deliveries()
            self.assertIn("1 uncertain deliveries", logs.output[0])
        async with self.database.transaction() as connection:
            states = list(
                await connection.scalars(
                    select(birthday_deliveries.c.status).order_by(
                        birthday_deliveries.c.user_id
                    )
                )
            )
        self.assertEqual(states, ["obsolete", "uncertain", "sent"])
        for uid, expected in ((2, True), (3, False), (4, False)):
            fresh = BirthdayDelivery(f"retry-{uid}", 1, uid, claims[0].today, 1, 1)
            self.assertEqual(await self.repo.claim_delivery(fresh), expected)
        self.assertFalse(await self.repo.begin_delivery(claims[0]))

    async def test_release_cannot_change_newer_claim_uncertain_or_sent(self) -> None:
        await self.repo.set_user_birthday(1, "Guild", 10, 2, "Member", "06-10-2000")
        old = BirthdayDelivery("old", 1, 2, date(2026, 10, 6), 1, 1)
        self.assertTrue(await self.repo.claim_delivery(old))
        await self.repo.release_delivery(old)
        fresh = BirthdayDelivery("fresh", 1, 2, old.today, 1, 1)
        self.assertTrue(await self.repo.claim_delivery(fresh))
        await self.repo.release_delivery(old)
        self.assertTrue(await self.repo.begin_delivery(fresh))
        await self.repo.release_delivery(fresh)
        await self.repo.recover_deliveries()
        blocked = BirthdayDelivery("blocked", 1, 2, old.today, 1, 1)
        self.assertFalse(await self.repo.claim_delivery(blocked))
        await self.repo.finish_delivery(fresh, 100)
        await self.repo.release_delivery(fresh)
        self.assertFalse(await self.repo.claim_delivery(blocked))
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(birthday_deliveries.c.status)), "sent"
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

    async def test_obsolete_claim_is_rearmed_once_and_old_token_is_inert(self) -> None:
        for change in ("settings", "birthday"):
            with self.subTest(change=change):
                gid = 10 if change == "settings" else 20
                await self.repo.set_user_birthday(
                    gid, "Guild", gid, 2, "Member", "06-10-2000"
                )
                old = BirthdayDelivery(f"old-{gid}", gid, 2, date(2026, 10, 6), 1, 1)
                self.assertTrue(await self.repo.claim_delivery(old))
                if change == "settings":
                    await self.repo.configure_guild(gid, "Guild", gid + 1, None)
                else:
                    await self.repo.set_user_birthday(
                        gid, "Guild", gid, 2, "Member", "06-10-2001"
                    )
                self.assertFalse(await self.repo.begin_delivery(old))
                fresh = [
                    BirthdayDelivery(
                        f"fresh-{gid}-{index}",
                        gid,
                        2,
                        old.today,
                        2 if change == "settings" else 1,
                        2 if change == "birthday" else 1,
                    )
                    for index in range(2)
                ]
                results = await asyncio.gather(
                    *(self.repo.claim_delivery(item) for item in fresh)
                )
                self.assertEqual(sum(results), 1)
                winner = fresh[results.index(True)]
                self.assertFalse(await self.repo.begin_delivery(old))
                await self.repo.finish_delivery(old, 100)
                self.assertTrue(await self.repo.begin_delivery(winner))
                self.assertFalse(
                    await self.repo.claim_delivery(fresh[1 - results.index(True)])
                )
                await self.repo.finish_delivery(old, 101)
                await self.repo.finish_delivery(winner, 102)
                self.assertFalse(await self.repo.claim_delivery(winner))
                loaded = await self.repo.get(gid)
                if loaded is None:
                    self.fail("Missing birthday guild")
                self.assertEqual(loaded.users[2].was_congrats, ["06-10-2026"])

    async def test_failed_rearm_rolls_back_and_can_be_retried(self) -> None:
        await self.repo.set_user_birthday(1, "Guild", 10, 2, "Member", "06-10-2000")
        old = BirthdayDelivery("old", 1, 2, date(2026, 10, 6), 1, 1)
        self.assertTrue(await self.repo.claim_delivery(old))
        await self.repo.configure_guild(1, "Guild", 20, None)
        self.assertFalse(await self.repo.begin_delivery(old))
        fresh = BirthdayDelivery("fresh", 1, 2, old.today, 2, 1)
        async with self.database.transaction() as connection:
            await connection.exec_driver_sql(
                """CREATE TRIGGER reject_rearm AFTER UPDATE ON birthday_deliveries
                WHEN NEW.status = 'claimed' BEGIN
                    SELECT RAISE(ABORT, 'injected');
                END"""
            )
        with self.assertRaises(IntegrityError):
            await self.repo.claim_delivery(fresh)
        async with self.database.transaction() as connection:
            row = (
                await connection.execute(
                    select(
                        birthday_deliveries.c.operation_id, birthday_deliveries.c.status
                    )
                )
            ).one()
            self.assertEqual(row, ("old", "obsolete"))
            await connection.exec_driver_sql("DROP TRIGGER reject_rearm")
        self.assertTrue(await self.repo.claim_delivery(fresh))
