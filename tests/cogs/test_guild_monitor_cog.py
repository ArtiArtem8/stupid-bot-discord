"""Monitoring startup and manual restore honor the manager's validity policy."""

import unittest
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.guild_monitoring import (
    ServerMonitoringManager,
)
from api.monitor_models import MemberSnapshot
from cogs.guild_monitor_cog import ServerMonitorCog
from framework.feedback_ui import FeedbackType, FeedbackUI
from repositories.monitor_repository import MonitorRepository
from tests.storage import temporary_database


class TestGuildMonitorCog(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.manager = ServerMonitoringManager(MonitorRepository(database))

    async def test_join_logs_when_every_role_is_skipped(self) -> None:
        member = MagicMock(spec=discord.Member, id=5, bot=False)
        member.guild = MagicMock(spec=discord.Guild, id=10)
        await self.manager.set_enabled(10, True)
        cog = ServerMonitorCog(MagicMock(), self.manager)
        with (
            patch.object(
                self.manager, "restore_snapshot", new=AsyncMock(return_value=([], [7]))
            ),
            self.assertLogs("cogs.guild_monitor_cog", level="WARNING") as logs,
        ):
            await cog.on_member_join(member)
        self.assertIn("guild=10 user=5 restored=0 skipped=[7]", logs.output[0])

    async def test_zero_success_restore_reports_warning_without_claiming_success(
        self,
    ) -> None:
        item = MagicMock(spec=discord.Interaction)
        item.guild = MagicMock(spec=discord.Guild, id=10)
        item.response = MagicMock(spec=discord.InteractionResponse)
        member = MagicMock(spec=discord.Member, id=5, display_name="Member")
        cog = ServerMonitorCog(MagicMock(), self.manager)
        snapshot = MemberSnapshot(5, "Member", [7], datetime.now(UTC), 1, 1)
        with (
            patch.object(
                self.manager,
                "get_snapshot",
                new=AsyncMock(return_value=snapshot),
            ),
            patch.object(
                self.manager,
                "restore_snapshot",
                new=AsyncMock(return_value=([], [7])),
            ),
            patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
        ):
            await cog.monitor_restore._do_call(item, {"user": member})
        send.assert_awaited_once()
        self.assertEqual(send.call_args.kwargs["feedback_type"], FeedbackType.WARNING)
        self.assertEqual(send.call_args.kwargs["title"], "Восстановление ролей: Member")

    async def test_cleanup_waits_for_ready(self) -> None:
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        cog = ServerMonitorCog(bot, self.manager)
        await cog.before_cleanup_task()
        bot.wait_until_ready.assert_awaited_once()

    async def test_manual_restore_does_not_grant_expired_snapshot(self) -> None:
        with TemporaryDirectory():
            manager = self.manager
            await manager.set_enabled(10, True, 1)
            member = MagicMock(spec=discord.Member, id=5, bot=False)
            member.guild = MagicMock(spec=discord.Guild, id=10)
            role = MagicMock(spec=discord.Role, id=7, managed=False)
            role.is_default.return_value = False
            role.is_premium_subscriber.return_value = False
            member.roles = [role]
            with patch(
                "api.guild_monitoring.utcnow",
                return_value=datetime.now(UTC) - timedelta(days=30),
            ):
                await manager.save_snapshot(member)
            member.roles = []
            item = MagicMock(spec=discord.Interaction)
            item.guild = member.guild
            item.response = MagicMock(spec=discord.InteractionResponse)
            cog = ServerMonitorCog(MagicMock(), self.manager)
            with (
                patch.object(cog, "manager", manager),
                patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
            ):
                await cog.monitor_restore._do_call(item, {"user": member})
            member.add_roles.assert_not_awaited()
            self.assertIsNone(await manager.get_snapshot(10, 5))
            self.assertEqual(send.call_args.kwargs["feedback_type"], FeedbackType.INFO)
