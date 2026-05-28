"""Tests for music cog startup when Lavalink is unavailable."""

import asyncio
import unittest
from typing import Any, cast, override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from api.music.models import (
    MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
    MusicResult,
    MusicResultStatus,
    VoiceCheckResult,
)
from api.music.service import CoreMusicService
from cogs.music.music_cog import MusicCog, _format_voice_result_message


class _ResponseStub:
    def __init__(self) -> None:
        self.type: discord.InteractionResponseType | None = None
        self.send_message = AsyncMock()
        self.defer = AsyncMock(side_effect=self._defer)

    def is_done(self) -> bool:
        return self.type is not None

    async def _defer(self, **_: Any) -> None:
        self.type = discord.InteractionResponseType.deferred_channel_message


class TestMusicCogAvailability(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.cog = MusicCog.__new__(MusicCog)
        self.bot_mock = MagicMock()
        self.cog.bot = cast(commands.Bot, self.bot_mock)
        self.service_initialize = AsyncMock(return_value=None)
        service_mock = MagicMock()
        service_mock.initialize = self.service_initialize
        self.cog.service = cast(CoreMusicService, service_mock)
        self.auto_leave_start = MagicMock()
        auto_leave_monitor_mock = MagicMock()
        auto_leave_monitor_mock.start = self.auto_leave_start
        self.cog.auto_leave_monitor = auto_leave_monitor_mock

    async def test_on_ready_does_not_raise_when_service_init_is_soft(self) -> None:
        await self.cog.on_ready()

        self.service_initialize.assert_awaited_once()

    async def test_cog_load_does_not_raise_when_service_init_is_soft(self) -> None:
        self.bot_mock.is_ready.return_value = True

        await self.cog.cog_load()

        self.service_initialize.assert_awaited_once()
        self.auto_leave_start.assert_called_once()

    def test_unavailable_voice_message_has_no_raw_backend_details(self) -> None:
        message = _format_voice_result_message(
            VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE,
            None,
            None,
        )

        self.assertEqual(message, MUSIC_SERVICE_UNAVAILABLE_MESSAGE)
        self.assertNotIn("ClientConnectorError", message)
        self.assertNotIn("localhost", message)
        self.assertNotIn("traceback", message.lower())

    async def test_leave_uses_defer_budget_for_service_cleanup(self) -> None:
        guild = MagicMock()
        interaction = MagicMock()
        interaction.guild = guild
        interaction.response = _ResponseStub()
        interaction.followup.send = AsyncMock()
        message = MagicMock()
        message.delete = AsyncMock()
        interaction.edit_original_response = AsyncMock(return_value=message)
        self.cog.service.leave = AsyncMock(  # type: ignore[method-assign]
            return_value=MusicResult(MusicResultStatus.SUCCESS, "ok")
        )

        async def wait_for_operation(
            responder: object,
            operation: object,
            *,
            ephemeral: bool = False,
        ) -> MusicResult[None]:
            del responder, ephemeral
            return await cast(Any, operation)

        with patch(
            "cogs.music.music_cog.MusicInteractionResponder.await_with_defer_budget",
            autospec=True,
            side_effect=wait_for_operation,
        ) as await_with_defer_budget:
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        await_with_defer_budget.assert_awaited_once()
        self.cog.service.leave.assert_called_once_with(guild)  # type: ignore[attr-defined]

    async def test_leave_after_defer_edits_response_instead_of_send_message(
        self,
    ) -> None:
        guild = MagicMock()
        interaction = MagicMock()
        interaction.guild = guild
        interaction.response = _ResponseStub()
        interaction.followup.send = AsyncMock()
        message = MagicMock()
        message.delete = AsyncMock()
        interaction.edit_original_response = AsyncMock(return_value=message)

        async def slow_leave(_: object) -> MusicResult[None]:
            await asyncio.sleep(0)
            return MusicResult(MusicResultStatus.SUCCESS, "ok")

        self.cog.service.leave = AsyncMock(side_effect=slow_leave)  # type: ignore[method-assign]

        async def defer_then_wait(
            responder: object,
            operation: object,
            *,
            ephemeral: bool = False,
        ) -> MusicResult[None]:
            del responder, ephemeral
            await interaction.response.defer(thinking=True)
            return await cast(Any, operation)

        with patch(
            "cogs.music.music_cog.MusicInteractionResponder.await_with_defer_budget",
            autospec=True,
            side_effect=defer_then_wait,
        ):
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once()
