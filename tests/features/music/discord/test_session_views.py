"""Tests for music session views."""

import asyncio
import unittest
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
from discord import ui
from discord.webhook.async_ import async_context

from stupidbot.discord_ui.pagination import BasePaginator
from stupidbot.features.music.discord.views import SessionSummaryView
from stupidbot.features.music.discord.views.session import SessionPaginationAdapter
from stupidbot.features.music.runtime.session import MusicSession
from stupidbot.features.preferences.models import Preferences
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.policy import Language, PresentationContext


class TestSessionSummaryView(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.interaction = MagicMock(spec=discord.Interaction)
        self.interaction.id = 1
        self.interaction.type = discord.InteractionType.component
        self.interaction.extras = {}
        self.interaction.locale = discord.Locale.russian
        self.interaction.guild = MagicMock(spec=discord.Guild, id=1)
        self.interaction.guild_id = 1
        self.interaction.user.id = 42
        self.interaction.client = MagicMock()
        self.interaction.client.block_manager = MagicMock()
        self.interaction.client.block_manager.is_user_blocked = AsyncMock(
            return_value=False
        )
        self.preferences = MagicMock(spec=PreferencesRepository)
        self.preferences.get = AsyncMock(return_value=Preferences())
        self.interaction.client.preferences_repository = self.preferences
        self.interaction.response = discord.InteractionResponse(self.interaction)
        self.interaction.edit_original_response = AsyncMock(
            return_value=MagicMock(spec=discord.InteractionMessage)
        )
        self.adapter = MagicMock()
        self.adapter.create_interaction_response = AsyncMock(
            return_value={"interaction": {"id": str(self.interaction.id)}}
        )
        token = async_context.set(self.adapter)
        self.addCleanup(async_context.reset, token)

    def test_long_history_link_keeps_skip_marker_and_requester(self) -> None:
        uri = "https://example.com/" + "x" * 961
        session = MusicSession(guild_id=1)
        session.add_track("Song [Live]", uri, 42, 100, skipped=True)
        adapter = SessionPaginationAdapter(
            session, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )

        description = adapter.make_embed(0).description or ""

        self.assertLessEqual(len(description), 1024)
        self.assertIn(" • 1. ~~[", description)
        self.assertTrue(description.endswith(f"]({uri})~~ (<@42>)"))

    async def test_empty_session_shows_private_warning(self) -> None:
        view = SessionSummaryView(
            session=MusicSession(guild_id=1),
            context=PresentationContext(Language.RU, ZoneInfo("UTC")),
        )
        self.addCleanup(view.stop)

        with patch(
            "stupidbot.features.music.discord.views.session.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            history_button = next(
                child for child in view.children if isinstance(child, ui.Button)
            )
            await view._scheduled_task(history_button, self.interaction)

        send_warning.assert_awaited_once_with(
            self.interaction,
            "В этой сессии нет треков.",
            ephemeral=True,
        )

    async def test_history_defers_privately_before_preferences_then_shows_all_tracks(
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
        view = SessionSummaryView(
            session=session, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
        self.addCleanup(view.stop)
        waiting, release = asyncio.Event(), asyncio.Event()

        async def preferences(*_args: object) -> Preferences:
            waiting.set()
            await release.wait()
            return Preferences()

        self.preferences.get.side_effect = preferences
        history_button = next(
            child for child in view.children if isinstance(child, ui.Button)
        )
        task = asyncio.create_task(
            view._scheduled_task(history_button, self.interaction)
        )
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            self.adapter.create_interaction_response.assert_awaited_once()
            self.assertEqual(
                self.adapter.create_interaction_response.call_args.kwargs[
                    "params"
                ].payload,
                {"type": 5, "data": {"flags": 64}},
            )
            self.interaction.edit_original_response.assert_not_awaited()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)

        self.interaction.edit_original_response.assert_awaited_once()
        send_call = self.interaction.edit_original_response.await_args
        if send_call is None:
            self.fail("Session history did not send a paginator")
        send_kwargs = send_call.kwargs
        paginator = send_kwargs["view"]
        self.assertIsInstance(paginator, BasePaginator)
        self.addCleanup(paginator.stop)
        self.assertEqual(await paginator.get_total_pages(), 2)
        self.adapter.create_interaction_response.assert_awaited_once()

        descriptions = [
            paginator.data.make_embed(page).description
            for page in range(await paginator.get_total_pages())
        ]
        combined = "\n".join(description or "" for description in descriptions)
        for index in range(16):
            self.assertIn(f"Track {index}", combined)

    async def test_blocked_history_click_defers_then_refuses_without_paginator(
        self,
    ) -> None:
        session = MusicSession(guild_id=1)
        session.add_track("Track", "https://example.com/track", 42, 100)
        view = SessionSummaryView(
            session=session, context=PresentationContext(Language.RU, ZoneInfo("UTC"))
        )
        self.addCleanup(view.stop)
        waiting, release = asyncio.Event(), asyncio.Event()

        async def blocked(*_args: object) -> bool:
            waiting.set()
            await release.wait()
            return True

        self.interaction.client.block_manager.is_user_blocked.side_effect = blocked
        history_button = next(
            child for child in view.children if isinstance(child, ui.Button)
        )
        task = asyncio.create_task(
            view._scheduled_task(history_button, self.interaction)
        )
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            self.adapter.create_interaction_response.assert_awaited_once()
            self.assertEqual(
                self.adapter.create_interaction_response.call_args.kwargs[
                    "params"
                ].payload,
                {"type": 5, "data": {"flags": 64}},
            )
            self.interaction.edit_original_response.assert_not_awaited()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)

        self.interaction.edit_original_response.assert_awaited_once()
        response = self.interaction.edit_original_response.call_args.kwargs
        self.assertNotIsInstance(response.get("view"), BasePaginator)
        self.assertNotIn("Track", response["embed"].description)
        self.assertEqual(response["embed"].description, "Доступ к командам запрещён.")
        self.adapter.create_interaction_response.assert_awaited_once()

    async def test_timeout_removes_view_from_summary_message(self) -> None:
        view = SessionSummaryView(
            session=MusicSession(guild_id=1),
            context=PresentationContext(Language.RU, ZoneInfo("UTC")),
        )
        message = MagicMock()
        message.edit = AsyncMock()
        view.message = message

        await view.on_timeout()

        message.edit.assert_awaited_once_with(view=None)
