"""Monitoring startup and manual restore honor the manager's validity policy."""

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.guild_monitoring import (
    MemberSnapshot,
    ServerMonitoringManager,
    monitor_manager,
)
from cogs import guild_monitor_cog as cog_module
from cogs.guild_monitor_cog import ServerMonitorCog
from framework.feedback_ui import FeedbackType, FeedbackUI


class TestGuildMonitorCog(unittest.IsolatedAsyncioTestCase):
    async def test_zero_success_restore_reports_warning_without_claiming_success(
        self,
    ) -> None:
        item = MagicMock(spec=discord.Interaction)
        item.guild = MagicMock(spec=discord.Guild, id=10)
        item.response = MagicMock(spec=discord.InteractionResponse)
        member = MagicMock(spec=discord.Member, id=5, display_name="Member")
        cog = ServerMonitorCog(MagicMock())
        snapshot = MemberSnapshot(5, "Member", [7], datetime.now(UTC))
        with (
            patch.object(
                monitor_manager,
                "get_snapshot",
                new=AsyncMock(return_value=snapshot),
            ),
            patch.object(
                monitor_manager,
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
        cog = ServerMonitorCog(bot)
        await cog.before_cleanup_task()
        bot.wait_until_ready.assert_awaited_once()

    async def test_manual_restore_does_not_grant_expired_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            manager = ServerMonitoringManager(Path(directory))
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
            cog = ServerMonitorCog(MagicMock())
            with (
                patch.object(cog_module, "monitor_manager", manager),
                patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
            ):
                await cog.monitor_restore._do_call(item, {"user": member})
            member.add_roles.assert_not_awaited()
            self.assertIsNone(await manager.get_snapshot(10, 5))
            self.assertEqual(send.call_args.kwargs["feedback_type"], FeedbackType.INFO)
