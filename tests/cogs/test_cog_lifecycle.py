"""Regression tests for cog-owned background loops."""

import unittest
from unittest.mock import MagicMock, patch

from cogs.birthday_cog import BirthdayCog
from cogs.guild_monitor_cog import ServerMonitorCog


class TestCogLoopLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_birthday_loop_uses_cog_lifecycle(self) -> None:
        cog = BirthdayCog(MagicMock())
        loop = cog.birthday_timer
        self.assertIsNone(loop.get_task())

        with patch.object(loop, "start") as start:
            with patch.object(loop, "is_running", side_effect=(False, True)):
                await cog.cog_load()
                await cog.cog_load()
            start.assert_called_once()

        with (
            patch.object(loop, "is_running", return_value=True),
            patch.object(loop, "cancel") as cancel,
        ):
            await cog.cog_unload()
        cancel.assert_called_once()

    async def test_guild_monitor_loop_uses_cog_lifecycle(self) -> None:
        cog = ServerMonitorCog(MagicMock())
        loop = cog.cleanup_task
        self.assertIsNone(loop.get_task())

        with patch.object(loop, "start") as start:
            with patch.object(loop, "is_running", side_effect=(False, True)):
                await cog.cog_load()
                await cog.cog_load()
            start.assert_called_once()

        with (
            patch.object(loop, "is_running", return_value=True),
            patch.object(loop, "cancel") as cancel,
        ):
            await cog.cog_unload()
        cancel.assert_called_once()
