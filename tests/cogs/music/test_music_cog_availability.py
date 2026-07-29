"""Tests for music cog startup when Lavalink is unavailable."""

import asyncio
import unittest
from collections.abc import Awaitable
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
from framework import FeedbackType, FeedbackUI


class _ResponseStub:
    def __init__(self) -> None:
        self.type: discord.InteractionResponseType | None = None
        self.send_message = AsyncMock()
        self.defer = AsyncMock(side_effect=self._defer)

    def is_done(self) -> bool:
        return self.type is not None

    async def _defer(self, **_: Any) -> None:
        self.type = discord.InteractionResponseType.deferred_channel_message


async def _await_operation(
    _interaction: object,
    operation: Awaitable[MusicResult[None]],
    *,
    defer_after: float = 1.5,
    ephemeral: bool = False,
) -> MusicResult[None]:
    if defer_after != 1.5:
        raise AssertionError("Unexpected defer threshold")
    if not ephemeral:
        raise AssertionError("Leave flow must be ephemeral")
    return await operation


class TestMusicCogAvailability(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.cog = object.__new__(MusicCog)
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

    async def test_leave_uses_private_defer_flow_for_service_cleanup(self) -> None:
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
            flow_interaction: object,
            operation: object,
            *,
            defer_after: float = 1.5,
            ephemeral: bool = False,
        ) -> MusicResult[None]:
            self.assertIs(flow_interaction, interaction)
            self.assertEqual(defer_after, 1.5)
            self.assertTrue(ephemeral)
            return await cast(Any, operation)

        with patch(
            "cogs.music.music_cog.run_with_defer",
            side_effect=wait_for_operation,
        ) as run_flow:
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        run_flow.assert_awaited_once()
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
            _flow_interaction: object,
            operation: object,
            *,
            defer_after: float = 1.5,
            ephemeral: bool = False,
        ) -> MusicResult[None]:
            self.assertEqual(defer_after, 1.5)
            self.assertTrue(ephemeral)
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
            return await cast(Any, operation)

        with patch(
            "cogs.music.music_cog.run_with_defer",
            side_effect=defer_then_wait,
        ):
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=True,
        )
        interaction.response.send_message.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once()

    async def test_leave_success_sends_disconnected_message(self) -> None:
        guild = MagicMock()
        interaction = MagicMock(guild=guild)
        service_leave = AsyncMock(
            return_value=MusicResult(MusicResultStatus.SUCCESS, "Disconnected")
        )

        with (
            patch.object(self.cog.service, "leave", service_leave),
            patch(
                "cogs.music.music_cog.run_with_defer",
                side_effect=_await_operation,
            ),
            patch(
                "cogs.music.music_cog.send_info",
                new_callable=AsyncMock,
            ) as send_info,
            patch.object(
                self.cog,
                "_send_no_player_or_unavailable",
                new_callable=AsyncMock,
            ) as send_no_player,
            patch(
                "cogs.music.music_cog.send_error",
                new_callable=AsyncMock,
            ) as send_error,
        ):
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        send_info.assert_awaited_once_with(
            interaction,
            "Отключился",
            title="До свидания ❤️",
            ephemeral=True,
        )
        send_no_player.assert_not_awaited()
        send_error.assert_not_awaited()

    async def test_leave_not_connected_uses_no_player_warning(self) -> None:
        guild = MagicMock()
        interaction = MagicMock(guild=guild)
        result: MusicResult[None] = MusicResult(
            MusicResultStatus.FAILURE,
            "Not connected",
        )

        with (
            patch.object(
                self.cog.service,
                "leave",
                AsyncMock(return_value=result),
            ),
            patch(
                "cogs.music.music_cog.run_with_defer",
                side_effect=_await_operation,
            ),
            patch.object(FeedbackUI, "send", new=AsyncMock()) as send_feedback,
            patch(
                "cogs.music.music_cog.send_error",
                new_callable=AsyncMock,
            ) as send_error,
        ):
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        send_feedback.assert_awaited_once_with(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="Нет проигрывателя",
            ephemeral=True,
            title=None,
            delete_after=None,
        )
        send_error.assert_not_awaited()

    async def test_leave_disconnect_error_uses_send_error(self) -> None:
        guild = MagicMock()
        interaction = MagicMock(guild=guild)
        message = "Не удалось отключиться от голосового канала."
        result: MusicResult[None] = MusicResult(
            MusicResultStatus.ERROR,
            message,
        )

        with (
            patch.object(
                self.cog.service,
                "leave",
                AsyncMock(return_value=result),
            ),
            patch(
                "cogs.music.music_cog.run_with_defer",
                side_effect=_await_operation,
            ),
            patch.object(
                self.cog,
                "_send_no_player_or_unavailable",
                new_callable=AsyncMock,
            ) as send_no_player,
            patch(
                "cogs.music.music_cog.send_error",
                new_callable=AsyncMock,
            ) as send_error,
        ):
            await cast(Any, MusicCog.leave).callback(self.cog, interaction)

        send_error.assert_awaited_once_with(
            interaction,
            message,
            ephemeral=True,
        )
        send_no_player.assert_not_awaited()


class TestMusicCogJoinVisibility(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.cog = object.__new__(MusicCog)
        self.cog.service = MagicMock()

    async def test_join_service_operation_uses_private_defer_flow(self) -> None:
        interaction = MagicMock()
        guild = MagicMock()
        channel = MagicMock()
        expected = (VoiceCheckResult.SUCCESS, None)
        join = AsyncMock(return_value=expected)

        async def await_join(
            flow_interaction: object,
            operation: Awaitable[tuple[VoiceCheckResult, None]],
            *,
            defer_after: float = 1.5,
            ephemeral: bool = False,
        ) -> tuple[VoiceCheckResult, None]:
            self.assertIs(flow_interaction, interaction)
            self.assertEqual(defer_after, 1.5)
            self.assertTrue(ephemeral)
            return await operation

        with (
            patch.object(self.cog.service, "join", join),
            patch(
                "cogs.music.music_cog.run_with_defer",
                side_effect=await_join,
            ) as run_flow,
        ):
            result = await self.cog._join_for_join_command(
                interaction,
                guild,
                channel,
            )

        self.assertEqual(result, expected)
        join.assert_awaited_once_with(guild, channel)
        run_flow.assert_awaited_once()

    async def test_join_success_feedback_is_private_on_fast_path(self) -> None:
        interaction = MagicMock()
        channel = MagicMock()

        with patch(
            "cogs.music.music_cog.send_info",
            new=AsyncMock(),
        ) as send_info:
            await self.cog._send_join_feedback(
                interaction,
                VoiceCheckResult.SUCCESS,
                channel,
                None,
                ephemeral=True,
            )

        send_info.assert_awaited_once_with(
            interaction,
            _format_voice_result_message(
                VoiceCheckResult.SUCCESS,
                channel,
                None,
            ),
            delete_after=None,
            ephemeral=True,
        )

    async def test_join_failure_feedback_is_private_after_slow_flow(self) -> None:
        interaction = MagicMock()
        channel = MagicMock()

        with patch(
            "cogs.music.music_cog.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            await self.cog._send_join_feedback(
                interaction,
                VoiceCheckResult.USER_NOT_IN_VOICE,
                channel,
                None,
                warn_on_failure=True,
                ephemeral=True,
            )

        send_warning.assert_awaited_once_with(
            interaction,
            _format_voice_result_message(
                VoiceCheckResult.USER_NOT_IN_VOICE,
                channel,
                None,
            ),
            ephemeral=True,
            delete_after=None,
        )

    async def test_join_error_feedback_is_private(self) -> None:
        interaction = MagicMock()
        channel = MagicMock()

        with patch(
            "cogs.music.music_cog.send_error",
            new=AsyncMock(),
        ) as send_error:
            await self.cog._send_join_feedback(
                interaction,
                VoiceCheckResult.CONNECTION_FAILED,
                channel,
                None,
                ephemeral=True,
            )

        send_error.assert_awaited_once_with(
            interaction,
            _format_voice_result_message(
                VoiceCheckResult.CONNECTION_FAILED,
                channel,
                None,
            ),
            ephemeral=True,
        )

    async def test_play_join_failure_feedback_remains_public(self) -> None:
        interaction = MagicMock()
        channel = MagicMock()

        with patch.object(
            self.cog,
            "_send_join_feedback",
            new=AsyncMock(),
        ) as send_feedback:
            handled = await self.cog._handle_join_for_play(
                interaction,
                VoiceCheckResult.CONNECTION_FAILED,
                channel,
                None,
            )

        self.assertFalse(handled)
        send_feedback.assert_awaited_once_with(
            interaction,
            result=VoiceCheckResult.CONNECTION_FAILED,
            channel=channel,
            from_channel=None,
            delete_after=60,
            ephemeral=False,
        )
