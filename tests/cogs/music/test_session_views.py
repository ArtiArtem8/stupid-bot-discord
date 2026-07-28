"""Tests for music session views."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from discord import ui

from api.music import MusicSession
from cogs.music.views import SessionSummaryView
from framework import BasePaginator


class TestSessionSummaryView(unittest.IsolatedAsyncioTestCase):
    async def test_empty_session_shows_private_warning(self) -> None:
        view = SessionSummaryView(session=MusicSession(guild_id=1))
        interaction = MagicMock()

        with patch(
            "cogs.music.views.session.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            history_button = next(
                child for child in view.children if isinstance(child, ui.Button)
            )
            await history_button.callback(interaction)

        send_warning.assert_awaited_once_with(
            interaction,
            "В этой сессии нет треков.",
            ephemeral=True,
        )

    async def test_nonempty_session_sends_ephemeral_paginator_with_all_tracks(
        self,
    ) -> None:
        session = MusicSession(guild_id=1)
        for index in range(16):
            session.add_track(
                title=f"Track {index}",
                uri=f"https://example.com/{index}",
                requester_id=42,
                channel_id=100,
            )
        view = SessionSummaryView(session=session)
        interaction = MagicMock()
        interaction.user.id = 42
        send_message = AsyncMock(return_value=MagicMock(resource=None))

        with patch.object(interaction.response, "send_message", send_message):
            history_button = next(
                child for child in view.children if isinstance(child, ui.Button)
            )
            await history_button.callback(interaction)

        send_call = send_message.await_args
        if send_call is None:
            self.fail("Session history did not send a paginator")
        send_kwargs = send_call.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        self.assertTrue(send_kwargs["silent"])
        paginator = send_kwargs["view"]
        self.assertIsInstance(paginator, BasePaginator)
        self.assertEqual(await paginator.get_total_pages(), 2)

        descriptions = [
            paginator.data.make_embed(page).description
            for page in range(await paginator.get_total_pages())
        ]
        combined = "\n".join(description or "" for description in descriptions)
        for index in range(16):
            self.assertIn(f"Track {index}", combined)

    async def test_timeout_removes_view_from_summary_message(self) -> None:
        view = SessionSummaryView(session=MusicSession(guild_id=1))
        message = MagicMock()
        message.edit = AsyncMock()
        view.message = message

        await view.on_timeout()

        message.edit.assert_awaited_once_with(view=None)
