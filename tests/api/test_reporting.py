"""Reports commit before acknowledgement and deduplicate interaction retries."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from sqlalchemy import select

from api.reporting import ReportModal, _build_report_data, submit_report
from repositories.report_repository import ReportRepository
from repositories.sqlite.schema import reports
from tests.storage import temporary_database


class TestReporting(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repository = ReportRepository(self.database)
        self.interaction = MagicMock(spec=discord.Interaction, id=777)
        self.interaction.user = MagicMock(
            spec=discord.User, id=1, name="Author", avatar=None
        )
        self.interaction.user.name = "Author"
        self.interaction.guild = None
        self.interaction.channel = None
        self.interaction.message = None
        self.interaction.response = MagicMock(spec=discord.InteractionResponse)

    async def test_dm_report_and_settings_survive_without_a_guild(self) -> None:
        await self.repository.set_channel(99)
        report, channel = await submit_report(
            self.repository, self.interaction, "reason"
        )
        self.assertEqual(channel, 99)
        self.assertIsNone(report["guild"]["id"])
        repeated, channel = await submit_report(
            self.repository, self.interaction, "reason"
        )
        self.assertEqual(repeated, report)
        self.assertIsNone(channel)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            await submit_report(self.repository, self.interaction, "different reason")
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(reports.c.report_id, reports.c.created_us)
                )
            ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], report["report_id"])
        self.assertIsNotNone(rows[0][1])

    async def test_modal_persists_before_response_then_notifies(self) -> None:
        events: list[str] = []

        async def response(**_kwargs: object) -> None:
            async with self.database.transaction() as connection:
                self.assertIsNotNone(
                    await connection.scalar(select(reports.c.report_id))
                )
            events.append("response")

        async def notification(*_args: object) -> None:
            events.append("notification")

        modal = ReportModal(self.repository)
        modal.reason._value = "a useful report"
        self.interaction.response.send_message = AsyncMock(side_effect=response)
        with patch(
            "api.reporting._notify_report", new=AsyncMock(side_effect=notification)
        ):
            await modal.on_submit(self.interaction)
        self.assertEqual(events, ["response", "notification"])

    async def test_persistence_failure_cannot_send_success_or_notification(
        self,
    ) -> None:
        modal = ReportModal(self.repository)
        modal.reason._value = "a useful report"
        with patch.object(
            self.repository, "submit", side_effect=OSError("write failed")
        ):
            with self.assertRaises(OSError):
                await modal.on_submit(self.interaction)
        self.interaction.response.send_message.assert_not_awaited()

    def test_new_report_time_is_aware(self) -> None:
        from datetime import datetime

        value = _build_report_data(self.interaction, "reason")
        self.assertIsNotNone(datetime.fromisoformat(value["created_at"]).utcoffset())
