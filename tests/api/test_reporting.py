"""Tests for reporting helpers and submission flow.
Covers report payload building, persistence, and send dispatch.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.reporting import _build_report_data, submit_report


class TestReporting(unittest.IsolatedAsyncioTestCase):
    async def test_submit_report_appends_and_sends(self) -> None:
        user = SimpleNamespace(id=1, name="u", avatar=None)
        guild = SimpleNamespace(id=10, name="g")
        channel = SimpleNamespace(id=20, name="c")

        send_target = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_channel=Mock(return_value=send_target))

        interaction = SimpleNamespace(
            user=user,
            guild=guild,
            channel=channel,
            client=client,
        )

        fixed_dt = datetime(2025, 1, 1, 12, 0, 0)

        with (
            patch("api.reporting.get_json", return_value={"report_channel_id": 999}),
            patch("api.reporting.save_json") as save_mock,
            patch("api.reporting.datetime") as dt_mock,
            patch("api.reporting.uuid.uuid4", return_value="RID"),
            patch("api.reporting.discord.abc.Messageable", object),
        ):
            dt_mock.now.return_value = fixed_dt

            report_id = await submit_report(interaction, "reason")

        self.assertTrue(report_id)
        save_mock.assert_called_once()
        send_target.send.assert_awaited_once()

    def test_build_report_data_basic(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1, name="u", avatar=None),
            guild=None,
            channel=None,
        )
        data = _build_report_data(interaction, "r")
        self.assertEqual(data["reason"], "r")
        self.assertIsNone(data["guild"]["id"])
