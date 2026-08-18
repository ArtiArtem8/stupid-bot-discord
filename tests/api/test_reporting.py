"""Tests for reporting helpers and submission flow."""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import AsyncMock, Mock, patch

import discord

from api.reporting import (
    ReportModal,
    _build_report_data,
    set_report_channel,
    submit_report,
)
from utils import AsyncJsonFileStore


class TestReporting(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = AsyncJsonFileStore(
            Path(self.temp_dir.name) / "reports.json", backup_amount=0
        )

    async def test_submit_report_appends_and_returns_channel(self) -> None:
        user = SimpleNamespace(id=1, name="u", avatar=None)
        guild = SimpleNamespace(id=10, name="g")
        channel = SimpleNamespace(id=20, name="c")
        client = SimpleNamespace(get_channel=Mock())

        interaction = cast(
            discord.Interaction[discord.Client],
            cast(
                object,
                SimpleNamespace(
                    user=user,
                    guild=guild,
                    channel=channel,
                    client=client,
                ),
            ),
        )

        fixed_dt = datetime(2025, 1, 1, 12, 0, 0)

        with (
            patch("api.reporting._report_store", self.store),
            patch("api.reporting.datetime") as dt_mock,
            patch("api.reporting.uuid.uuid4", return_value="RID"),
        ):
            dt_mock.now.return_value = fixed_dt
            await set_report_channel(999)
            report, channel_id = await submit_report(interaction, "reason")

        data = await self.store.read()
        self.assertEqual(report["report_id"], "RID")
        self.assertEqual(channel_id, 999)
        self.assertEqual(data["reports"], [report])
        self.assertEqual(data["report_channel_id"], 999)
        client.get_channel.assert_not_called()

    async def test_report_and_channel_updates_preserve_each_other(self) -> None:
        interaction = cast(
            discord.Interaction[discord.Client],
            cast(
                object,
                SimpleNamespace(
                    user=SimpleNamespace(id=1, name="u", avatar=None),
                    guild=None,
                    channel=None,
                ),
            ),
        )

        with patch("api.reporting._report_store", self.store):
            await submit_report(interaction, "reason")
            await set_report_channel(123)

        data = await self.store.read()
        self.assertEqual(data["report_channel_id"], 123)
        self.assertEqual(len(cast(list[object], data["reports"])), 1)

    async def test_modal_responds_before_message_edit_and_notification(self) -> None:
        events: list[str] = []

        async def record_response(**_kwargs: object) -> None:
            events.append("respond")

        async def record_edit(**_kwargs: object) -> None:
            events.append("edit")

        async def record_notification(**_kwargs: object) -> None:
            events.append("notify")

        user = SimpleNamespace(id=1, name="u", avatar=None)
        message = SimpleNamespace(id=30, edit=AsyncMock(side_effect=record_edit))
        send_target = SimpleNamespace(send=AsyncMock(side_effect=record_notification))
        response_send = AsyncMock(side_effect=record_response)
        interaction = cast(
            discord.Interaction[discord.Client],
            cast(
                object,
                SimpleNamespace(
                    user=user,
                    guild=None,
                    channel=None,
                    client=SimpleNamespace(get_channel=Mock(return_value=send_target)),
                    message=message,
                    response=SimpleNamespace(send_message=response_send),
                ),
            ),
        )
        report = _build_report_data(interaction, "reason")

        async def persist_report(
            _interaction: object, _reason: str
        ) -> tuple[object, int]:
            events.append("persist")
            return report, 999

        modal = ReportModal()
        modal.reason._value = "reason"
        with (
            patch("api.reporting.submit_report", side_effect=persist_report),
            patch("api.reporting.discord.abc.Messageable", object),
        ):
            await modal.on_submit(interaction)

        self.assertEqual(events, ["persist", "respond", "edit", "notify"])
        response_send.assert_awaited_once()
        self.assertTrue(response_send.await_args.kwargs["ephemeral"])

    def test_build_report_data_basic(self) -> None:
        interaction = cast(
            discord.Interaction[discord.Client],
            cast(
                object,
                SimpleNamespace(
                    user=SimpleNamespace(id=1, name="u", avatar=None),
                    guild=None,
                    channel=None,
                ),
            ),
        )
        data = _build_report_data(interaction, "r")
        self.assertEqual(data["reason"], "r")
        self.assertIsNone(data["guild"]["id"])
