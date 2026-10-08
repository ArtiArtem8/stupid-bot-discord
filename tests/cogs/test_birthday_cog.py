"""Birthday roles follow the date policy independently of notification delivery."""

import asyncio
import unittest
from datetime import date
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from api.birthday import BirthdayManager
from api.birthday_models import BirthdayDelivery, BirthdayGuildConfig, BirthdayUser
from cogs import birthday_cog as birthday_module
from cogs.birthday_cog import BirthdayCog
from framework.feedback_ui import FeedbackUI
from repositories.birthday_repository import BirthdayRepository
from repositories.sqlite.schema import birthday_deliveries
from tests.storage import temporary_database


class TestBirthdayReconciliation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_channel_warns_once_until_recovery(self) -> None:
        self.bot.get_channel.return_value = None
        today = date(2027, 2, 28)
        with self.assertLogs("cogs.birthday_cog", level="WARNING") as logs:
            await self.cog._process_guild(42, today)
            await self.cog._process_guild(42, today)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("guild=42 channel=8", logs.output[0])
        self.bot.get_channel.return_value = self.channel
        with self.assertLogs("cogs.birthday_cog", level="INFO") as logs:
            await self.cog._process_guild(42, today)
        self.assertTrue(any("channel recovered" in line for line in logs.output))
        self.bot.get_channel.return_value = None
        with self.assertLogs("cogs.birthday_cog", level="WARNING"):
            await self.cog._process_guild(42, today)

    async def test_timer_retries_after_guild_listing_database_failure(self) -> None:
        self.bot.wait_until_ready = AsyncMock()
        calls = 0

        async def list_guilds() -> list[int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OperationalError("SELECT", {}, RuntimeError("database busy"))
            self.cog.birthday_timer.stop()
            return []

        self.manager.get_all_guild_ids = AsyncMock(side_effect=list_guilds)
        self.cog.birthday_timer.change_interval(seconds=0)
        with self.assertLogs("cogs.birthday_cog", level="ERROR"):
            task = self.cog.birthday_timer.start()
            try:
                await asyncio.wait_for(task, timeout=2)
            finally:
                await self.cog.cog_unload()
        self.assertEqual(calls, 2)
        self.assertFalse(self.cog.birthday_timer.failed())

    @override
    def setUp(self) -> None:
        self.bot = MagicMock()
        self.guild = MagicMock(spec=discord.Guild, id=42)
        self.role = MagicMock(spec=discord.Role, id=7)
        self.member = MagicMock(spec=discord.Member, id=10, roles=[])
        self.guild.roles = [self.role]
        self.guild.get_member.return_value = self.member
        self.bot.get_guild.return_value = self.guild
        self.channel = MagicMock(spec=discord.TextChannel)
        self.bot.get_channel.return_value = self.channel
        self.user = BirthdayUser(10, "User", "29-02-2000")
        self.cfg = BirthdayGuildConfig(42, "Guild", 8, {10: self.user}, 7)
        self.manager = MagicMock(spec=BirthdayManager)
        self.manager.repo = MagicMock()
        self.manager.repo.versions_current = AsyncMock(return_value=True)
        claimed: set[tuple[int, int, date]] = set()

        async def claim(value: BirthdayDelivery) -> bool:
            key = (value.guild_id, value.user_id, value.today)
            if key in claimed:
                return False
            claimed.add(key)
            return True

        self.manager.repo.claim_delivery = AsyncMock(side_effect=claim)
        self.manager.repo.begin_delivery = AsyncMock(return_value=True)
        self.manager.repo.release_delivery = AsyncMock()
        self.cog = BirthdayCog(self.bot, self.manager)

        async def add(role: discord.Role, **_kwargs: object) -> None:
            self.member.roles.append(role)

        async def remove(role: discord.Role, **_kwargs: object) -> None:
            self.member.roles.remove(role)

        async def record(claim: BirthdayDelivery, _message_id: int) -> None:
            self.user.add_congratulation(claim.today)

        self.member.add_roles.side_effect = add
        self.member.remove_roles.side_effect = remove
        config_patch = patch.object(
            self.manager, "get_guild_config", new=AsyncMock(return_value=self.cfg)
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.record = AsyncMock(side_effect=record)
        self.manager.repo.finish_delivery = self.record

    async def test_list_after_clearing_all_birthdays_sends_one_private_response(
        self,
    ) -> None:
        self.user.clear_birthday()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild

        with patch.object(FeedbackUI, "send", new_callable=AsyncMock) as feedback:
            await self.cog.list_birthdays._do_call(interaction, {"ephemeral": False})

        feedback.assert_awaited_once()
        response = feedback.await_args
        if response is None:
            self.fail("The birthday list did not produce a response")
        self.assertTrue(response.kwargs["ephemeral"])
        self.assertEqual(
            response.kwargs["description"],
            "На этом сервере нет сохранённых дней рождений.",
        )
        self.assertNotIn("embed", response.kwargs)
        self.assertIs(self.cfg.users[10], self.user)
        self.assertEqual(self.user.birthday, "")

    async def test_february_29_role_follows_non_leap_celebration(
        self,
    ) -> None:
        today = date(2027, 2, 28)
        await self.cog._process_guild(42, today)
        await self.cog._process_guild(42, today)
        self.assertIn(self.role, self.member.roles)
        self.member.add_roles.assert_awaited_once()
        self.member.remove_roles.assert_not_awaited()
        self.channel.send.assert_awaited_once()
        self.record.assert_awaited_once()
        await self.cog._process_guild(42, date(2027, 3, 1))
        self.assertNotIn(self.role, self.member.roles)
        self.member.remove_roles.assert_awaited_once()
        self.channel.send.assert_awaited_once()

    async def test_leap_year_does_not_celebrate_early(self) -> None:
        await self.cog._process_guild(42, date(2028, 2, 28))
        self.member.add_roles.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        await self.cog._process_guild(42, date(2028, 2, 29))
        self.member.add_roles.assert_awaited_once()
        self.channel.send.assert_awaited_once()

    async def test_congratulated_member_gets_missing_role_without_second_message(
        self,
    ) -> None:
        today = date(2027, 2, 28)
        self.user.add_congratulation(today)
        await self.cog._process_guild(42, today)
        self.member.add_roles.assert_awaited_once()
        self.channel.send.assert_not_awaited()
        self.record.assert_not_awaited()

    async def test_role_failure_is_retried_without_duplicate_congratulation(
        self,
    ) -> None:
        today = date(2027, 2, 28)
        add = self.member.add_roles.side_effect
        self.member.add_roles.side_effect = discord.Forbidden(
            MagicMock(status=403, reason="denied"), "hierarchy"
        )
        await self.cog._process_guild(42, today)
        self.assertNotIn(self.role, self.member.roles)
        self.channel.send.assert_awaited_once()
        self.member.add_roles.side_effect = add
        await self.cog._process_guild(42, today)
        self.assertIn(self.role, self.member.roles)
        self.channel.send.assert_awaited_once()

    async def test_missing_channel_does_not_prevent_role_reconciliation(self) -> None:
        self.bot.get_channel.return_value = None
        await self.cog._process_guild(42, date(2027, 2, 28))
        self.member.add_roles.assert_awaited_once()
        self.record.assert_not_awaited()

    async def test_unknown_message_outcome_is_not_recorded_or_blindly_retried(
        self,
    ) -> None:
        today = date(2027, 2, 28)
        self.channel.send.side_effect = discord.HTTPException(
            MagicMock(status=503, reason="unavailable"), "retry"
        )
        with self.assertLogs("cogs.birthday_cog", level="ERROR"):
            await self.cog._process_guild(42, today)
        self.record.assert_not_awaited()
        self.channel.send.side_effect = None
        await self.cog._process_guild(42, today)
        self.record.assert_not_awaited()
        self.channel.send.assert_awaited_once()


class TestBirthdayDeliveryRecovery(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repo = BirthdayRepository(self.database)
        await self.repo.set_user_birthday(42, "Guild", 8, 10, "User", "06-10-2000")
        self.bot = MagicMock()
        self.guild = MagicMock(spec=discord.Guild, id=42)
        self.member = MagicMock(spec=discord.Member, id=10)
        self.guild.get_member.return_value = self.member
        self.channel = MagicMock(spec=discord.TextChannel, id=8)
        self.channel.send.return_value = MagicMock(id=99)
        self.user = BirthdayUser(10, "User", "06-10-2000")
        self.cog = BirthdayCog(self.bot, BirthdayManager(self.repo))

    async def _attempt(self) -> None:
        await self.cog._handle_birthday(
            self.guild, self.channel, self.user, date(2026, 10, 6), 1
        )

    async def _status(self) -> str | None:
        async with self.database.transaction() as connection:
            return await connection.scalar(select(birthday_deliveries.c.status))

    async def test_pre_send_exception_allows_next_attempt(self) -> None:
        with (
            patch.object(
                birthday_module, "SafeEmbed", side_effect=RuntimeError("prepare")
            ),
            self.assertLogs("cogs.birthday_cog", level="ERROR"),
        ):
            await self._attempt()
        self.assertEqual(await self._status(), "obsolete")
        self.channel.send.assert_not_awaited()
        await self._attempt()
        self.channel.send.assert_awaited_once()
        self.assertEqual(await self._status(), "sent")

    async def test_cancellation_before_begin_releases_claim(self) -> None:
        entered = asyncio.Event()
        hold = asyncio.Event()

        async def begin(_claim: BirthdayDelivery) -> bool:
            entered.set()
            await hold.wait()
            return False

        with patch.object(self.repo, "begin_delivery", side_effect=begin):
            task = asyncio.create_task(self._attempt())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self._status(), "obsolete")
        self.channel.send.assert_not_awaited()
        await self._attempt()
        self.channel.send.assert_awaited_once()

    async def test_cancelled_claim_acknowledgement_releases_committed_claim(
        self,
    ) -> None:
        original = self.repo.claim_delivery

        async def claim(value: BirthdayDelivery) -> bool:
            await original(value)
            raise asyncio.CancelledError

        with patch.object(self.repo, "claim_delivery", side_effect=claim):
            with self.assertRaises(asyncio.CancelledError):
                await self._attempt()
        self.assertEqual(await self._status(), "obsolete")
        await self._attempt()
        self.channel.send.assert_awaited_once()

    async def test_lost_begin_acknowledgement_remains_uncertain(self) -> None:
        original = self.repo.begin_delivery

        async def begin(value: BirthdayDelivery) -> bool:
            await original(value)
            raise RuntimeError("commit acknowledgement lost")

        with (
            patch.object(self.repo, "begin_delivery", side_effect=begin),
            self.assertLogs("cogs.birthday_cog", level="ERROR"),
        ):
            await self._attempt()
        self.assertEqual(await self._status(), "uncertain")
        await self._attempt()
        self.channel.send.assert_not_awaited()

    async def test_cancelled_send_remains_uncertain(self) -> None:
        entered = asyncio.Event()
        hold = asyncio.Event()

        async def send(**_kwargs: object) -> None:
            entered.set()
            await hold.wait()

        self.channel.send.side_effect = send
        task = asyncio.create_task(self._attempt())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self._status(), "uncertain")
        await self._attempt()
        self.channel.send.assert_awaited_once()
