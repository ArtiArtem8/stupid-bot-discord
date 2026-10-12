"""Reports commit before success feedback and deduplicate interaction retries."""

import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
from discord.webhook.async_ import async_context
from sqlalchemy import select

from stupidbot.discord_ui.theme import Color
from stupidbot.features.reports.repository import ReportRepository
from stupidbot.features.reports.ui import (
    ReportModal,
    _build_report_data,
    _create_report_embed,
    _notify_report,
    submit_report,
)
from stupidbot.i18n.policy import Language, PresentationContext
from stupidbot.storage.schema import reports
from tests.storage.fixtures import temporary_database


class TestReporting(unittest.IsolatedAsyncioTestCase):
    async def test_unnamed_locations_are_localized_after_persistence(self) -> None:
        for channel, ru, en in (
            (None, "Неизвестный канал", "Unknown channel"),
            (
                MagicMock(spec=discord.DMChannel, id=42),
                "Личные сообщения",
                "Direct messages",
            ),
        ):
            with self.subTest(channel=channel):
                self.interaction.id += 1
                self.interaction.channel = channel
                report, _ = await submit_report(
                    self.repository, self.interaction, "reason"
                )
                self.assertIsNone(report["channel"]["name"])
                for language, expected in ((Language.RU, ru), (Language.EN, en)):
                    embed = _create_report_embed(report, language=language)
                    location = embed.fields[-1].value
                    if location is None:
                        self.fail("Expected the report location")
                    self.assertIn(expected, location)

    def test_saved_channel_names_are_never_reinterpreted_as_labels(self) -> None:
        report = _build_report_data(self.interaction, "reason")
        for name in ("Unknown", "DM with Author", "Канал пользователя"):
            report["channel"]["name"] = name
            for language in Language:
                with self.subTest(name=name, language=language):
                    embed = _create_report_embed(report, language=language)
                    location = embed.fields[-1].value
                    if location is None:
                        self.fail("Expected the saved report location")
                    self.assertTrue(location.endswith(name))

    async def test_modal_keeps_language_and_preserves_report_text(self) -> None:
        modal = ReportModal(
            self.repository, context=PresentationContext(Language.EN, ZoneInfo("UTC"))
        )
        modal.reason._value = "Проблема с музыкой"
        self.interaction.locale = discord.Locale.russian
        self.interaction.response.defer = AsyncMock()
        await modal.on_submit(self.interaction)
        embed = self.interaction.edit_original_response.call_args.kwargs["embed"]
        self.assertEqual(embed.title, "Thank you for the report!")
        self.assertIn("Your personal ID:", embed.description)
        async with self.database.transaction() as connection:
            self.assertEqual(
                await connection.scalar(select(reports.c.reason)), "Проблема с музыкой"
            )

    def _prepare_modal_response(self) -> MagicMock:
        self.interaction.type = discord.InteractionType.modal_submit
        self.interaction.response = discord.InteractionResponse(self.interaction)
        adapter = MagicMock()
        adapter.create_interaction_response = AsyncMock(
            return_value={"interaction": {"id": str(self.interaction.id)}}
        )
        return adapter

    async def test_modal_dispatch_resolves_deferred_storage_failure(self) -> None:
        adapter = self._prepare_modal_response()
        modal = ReportModal(
            self.repository, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
        modal.reason._value = "a useful report"
        token = async_context.set(adapter)
        try:
            with (
                patch.object(
                    self.repository,
                    "submit",
                    side_effect=OSError("sensitive storage failure"),
                ),
                patch(
                    "stupidbot.features.reports.ui._notify_report",
                    new_callable=AsyncMock,
                ) as notify,
                self.assertLogs("stupidbot.features.reports.ui", level="ERROR"),
            ):
                await modal._scheduled_task(self.interaction, [], {})
        finally:
            async_context.reset(token)

        self.interaction.edit_original_response.assert_awaited_once()
        reply = self.interaction.edit_original_response.await_args
        if reply is None:
            self.fail("Expected a final response for the failed report")
        embed = reply.kwargs["embed"]
        self.assertEqual(embed.color.value, Color.ERROR)
        self.assertNotIn("sensitive storage failure", str(embed.to_dict()))
        self.assertNotIn("view", reply.kwargs)
        self.assertEqual(
            adapter.create_interaction_response.call_args.kwargs["params"].payload,
            {"type": 5, "data": {"flags": 64}},
        )
        notify.assert_not_awaited()

    async def test_modal_defers_a_separate_private_confirmation(self) -> None:
        adapter = self._prepare_modal_response()
        self.interaction.message = MagicMock(edit=AsyncMock())
        modal = ReportModal(
            self.repository, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
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
        modal = ReportModal(
            self.repository, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
        modal.reason._value = "a useful report"
        self.interaction.edit_original_response = AsyncMock(
            side_effect=RuntimeError("lost reply")
        )
        with patch(
            "stupidbot.features.reports.ui._notify_report", new_callable=AsyncMock
        ) as notify:
            with self.assertRaisesRegex(RuntimeError, "lost reply"):
                await modal.on_submit(self.interaction)
        self.interaction.response.defer.assert_awaited_once_with(
            ephemeral=True, thinking=True
        )
        notify.assert_awaited_once()
        async with self.database.transaction() as connection:
            self.assertIsNotNone(await connection.scalar(select(reports.c.report_id)))

    async def test_modal_dispatch_keeps_postcommit_failure_feedback_safe(self) -> None:
        for failing_step in ("reply", "notification"):
            with self.subTest(failing_step=failing_step):
                self.interaction.id += 1
                adapter = self._prepare_modal_response()
                error = RuntimeError("sensitive remote failure")
                self.interaction.edit_original_response = AsyncMock(
                    side_effect=[error, None] if failing_step == "reply" else None
                )
                modal = ReportModal(
                    self.repository,
                    context=PresentationContext(Language.RU, ZoneInfo("UTC")),
                )
                modal.reason._value = "a useful report"
                token = async_context.set(adapter)
                try:
                    with (
                        patch(
                            "stupidbot.features.reports.ui._notify_report",
                            new=AsyncMock(
                                side_effect=error
                                if failing_step == "notification"
                                else None
                            ),
                        ) as notify,
                        self.assertLogs("stupidbot.features.reports.ui", level="ERROR"),
                    ):
                        await modal._scheduled_task(self.interaction, [], {})
                finally:
                    async_context.reset(token)

                notify.assert_awaited_once()
                self.assertEqual(self.interaction.edit_original_response.await_count, 2)
                reply = self.interaction.edit_original_response.await_args
                if reply is None:
                    self.fail("Expected a safe final error response")
                embed = reply.kwargs["embed"]
                self.assertEqual(embed.color.value, Color.ERROR)
                self.assertEqual(
                    embed.description,
                    "Не удалось завершить обработку отчёта. Детали записаны в лог.",
                )
                self.assertNotIn("sensitive remote failure", str(embed.to_dict()))
                async with self.database.transaction() as connection:
                    self.assertIsNotNone(
                        await connection.scalar(
                            select(reports.c.report_id).where(
                                reports.c.request_key == str(self.interaction.id)
                            )
                        )
                    )

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
        with self.assertLogs("stupidbot.features.reports.ui", level="WARNING") as logs:
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

        modal = ReportModal(
            self.repository, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
        modal.reason._value = "a useful report"
        self.interaction.edit_original_response = AsyncMock(side_effect=response)
        with patch(
            "stupidbot.features.reports.ui._notify_report",
            new=AsyncMock(side_effect=notification),
        ):
            await modal.on_submit(self.interaction)
        self.assertEqual(events, ["response", "notification"])

    async def test_persistence_failure_cannot_send_success_or_notification(
        self,
    ) -> None:
        modal = ReportModal(
            self.repository, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
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
