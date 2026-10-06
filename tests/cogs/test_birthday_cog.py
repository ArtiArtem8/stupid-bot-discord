"""Birthday roles follow the date policy independently of notification delivery."""

import unittest
from datetime import date
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.birthday import BirthdayManager
from api.birthday_models import BirthdayDelivery, BirthdayGuildConfig, BirthdayUser
from cogs.birthday_cog import BirthdayCog


class TestBirthdayReconciliation(unittest.IsolatedAsyncioTestCase):
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
