"""Reports commit before success feedback and deduplicate interaction retries."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.webhook.async_ import async_context
from sqlalchemy import select

from api.reporting import ReportModal, _build_report_data, _notify_report, submit_report
from repositories.report_repository import ReportRepository
from repositories.sqlite.schema import reports
from tests.storage import temporary_database


class TestReporting(unittest.IsolatedAsyncioTestCase):
    async def test_modal_defers_a_separate_private_confirmation(self) -> None:
        self.interaction.type = discord.InteractionType.modal_submit
        self.interaction.response = discord.InteractionResponse(self.interaction)
        self.interaction.message = MagicMock(edit=AsyncMock())
        adapter = MagicMock()
        adapter.create_interaction_response = AsyncMock(
            return_value={"interaction": {"id": "777"}}
        )
        modal = ReportModal(self.repository)
        modal.reason._value = "a useful report"
        token = async_context.set(adapter)
        try:
            await modal.on_submit(self.interaction)
        finally:
            async_context.reset(token)
        self.assertEqual(
            adapter.create_interaction_response.call_args.kwargs["params"].payload,
            {"type": 5, "data": {"flags": 64}},
        )
        self.interaction.message.edit.assert_awaited_once_with(view=None)
        self.interaction.edit_original_response.assert_awaited_once()

    async def test_failed_final_response_still_notifies_committed_report(self) -> None:
        modal = ReportModal(self.repository)
        modal.reason._value = "a useful report"
        self.interaction.edit_original_response = AsyncMock(
            side_effect=RuntimeError("lost reply")
        )
        with patch("api.reporting._notify_report", new_callable=AsyncMock) as notify:
            with self.assertRaisesRegex(RuntimeError, "lost reply"):
                await modal.on_submit(self.interaction)
        self.interaction.response.defer.assert_awaited_once_with(
            ephemeral=True, thinking=True
        )
        notify.assert_awaited_once()
        async with self.database.transaction() as connection:
            self.assertIsNotNone(await connection.scalar(select(reports.c.report_id)))

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

    async def test_missing_notification_channel_logs_persisted_report_id(self) -> None:
        await self.repository.set_channel(99)
        report, channel = await submit_report(
            self.repository, self.interaction, "reason"
        )
        self.interaction.client.get_channel.return_value = None
        with self.assertLogs("api.reporting", level="WARNING") as logs:
            await _notify_report(self.interaction, report, channel)
        self.assertIn(report["report_id"], logs.output[0])
        self.assertIn("channel=99", logs.output[0])

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
        self.interaction.edit_original_response = AsyncMock(side_effect=response)
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
        self.interaction.edit_original_response.assert_not_awaited()

    def test_new_report_time_is_aware(self) -> None:
        from datetime import datetime

        value = _build_report_data(self.interaction, "reason")
        self.assertIsNotNone(datetime.fromisoformat(value["created_at"]).utcoffset())
