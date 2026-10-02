"""Birthday roles follow the date policy independently of notification delivery."""

import unittest
from datetime import date
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.birthday import birthday_manager
from api.birthday_models import BirthdayGuildConfig, BirthdayUser
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
        self.cog = BirthdayCog(self.bot)

        async def add(role: discord.Role, **_kwargs: object) -> None:
            self.member.roles.append(role)

        async def remove(role: discord.Role, **_kwargs: object) -> None:
            self.member.roles.remove(role)

        async def record(_guild_id: int, _user_id: int, today: date) -> None:
            self.user.add_congratulation(today)

        self.member.add_roles.side_effect = add
        self.member.remove_roles.side_effect = remove
        config_patch = patch.object(
            birthday_manager, "get_guild_config", new=AsyncMock(return_value=self.cfg)
        )
        config_patch.start()
        self.addCleanup(config_patch.stop)
        record_patch = patch.object(
            birthday_manager, "record_congratulation", new=AsyncMock(side_effect=record)
        )
        self.record = record_patch.start()
        self.addCleanup(record_patch.stop)

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
        self.record.assert_awaited_once_with(42, 10, today)
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

    async def test_failed_message_is_not_recorded_and_can_be_retried(self) -> None:
        today = date(2027, 2, 28)
        self.channel.send.side_effect = discord.HTTPException(
            MagicMock(status=503, reason="unavailable"), "retry"
        )
        with self.assertLogs("cogs.birthday_cog", level="ERROR"):
            await self.cog._process_guild(42, today)
        self.record.assert_not_awaited()
        self.channel.send.side_effect = None
        await self.cog._process_guild(42, today)
        self.record.assert_awaited_once_with(42, 10, today)
