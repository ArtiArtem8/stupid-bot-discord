"""Tests for track controller lifecycle and component acknowledgement."""

import asyncio
import unittest
from typing import cast, override
from unittest.mock import AsyncMock, MagicMock, call, patch

import aiohttp
import discord
from discord import Client, Interaction, ui

from api.music.models import ControllerDestroyReason, PlaybackAttempt
from cogs.music.views import TrackControllerManager, TrackControllerView
from cogs.music.views import controller as controller_module
from framework.feedback_ui import FeedbackType, FeedbackUI
from tests.api.music.helpers import make_entry


class TestTrackControllerManager(unittest.IsolatedAsyncioTestCase):
    async def test_stale_view_stop_does_not_remove_new_controller(self) -> None:
        manager = TrackControllerManager(MagicMock(), MagicMock())
        stale_view = MagicMock()
        current_view = MagicMock()
        manager.controllers[1] = current_view
        cleanup_existing = AsyncMock()

        with patch.object(manager, "_cleanup_existing", cleanup_existing):
            await manager.destroy_for_guild(
                1,
                ControllerDestroyReason.STALE_VIEW,
                requesting_view=stale_view,
            )

        cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)

    async def test_old_track_destroy_does_not_remove_new_controller(self) -> None:
        manager = TrackControllerManager(MagicMock(), MagicMock())
        current_view = MagicMock(attempt_id=2)
        manager.controllers[1] = current_view
        cleanup_existing = AsyncMock()

        with patch.object(manager, "_cleanup_existing", cleanup_existing):
            await manager.destroy_for_guild(
                1,
                ControllerDestroyReason.TRACK_END,
                expected_attempt_id=1,
            )

        cleanup_existing.assert_not_awaited()
        self.assertIs(manager.controllers[1], current_view)

    async def test_create_for_user_replaces_existing_controller_and_message(
        self,
    ) -> None:
        connection = MagicMock()
        connection.invalidate_player = AsyncMock()
        manager = TrackControllerManager(MagicMock(), connection)
        old_view = MagicMock()
        manager.controllers[1] = old_view
        manager._active_messages[1] = (10, 20)

        attempt = PlaybackAttempt(2, make_entry("new-track"))
        new_player = MagicMock(current_attempt=attempt)
        message = MagicMock(id=21)
        message.channel.id = 10
        channel = MagicMock()
        channel.send = AsyncMock(return_value=message)
        new_view = MagicMock()
        safe_delete_message = AsyncMock()

        with (
            patch.object(manager, "_safe_delete_message", safe_delete_message),
            patch(
                "cogs.music.views.controller.TrackControllerView", return_value=new_view
            ) as view_cls,
        ):
            await manager.create_for_user(
                guild_id=1,
                user_id=2,
                channel=channel,
                player=new_player,
                attempt=attempt,
            )

        old_view.stop.assert_called_once()
        safe_delete_message.assert_awaited_once_with(10, 20)
        self.assertEqual(manager.controllers, {1: new_view})
        self.assertEqual(manager._active_messages, {1: (10, 21)})
        self.assertEqual(
            view_cls.call_args.kwargs["on_player_failure"],
            connection.invalidate_player,
        )
        self.assertIs(view_cls.call_args.kwargs["attempt"], attempt)


class TestControllerMessageCleanup(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.message = MagicMock(id=20)
        self.message.delete = AsyncMock()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.get_partial_message.return_value = self.message
        self.bot = MagicMock()
        self.bot.get_channel.return_value = self.channel
        self.bot.fetch_channel = AsyncMock(return_value=self.channel)
        self.manager = TrackControllerManager(self.bot, MagicMock())
        self.old_view = MagicMock(attempt_id=1)
        self.manager.controllers[1] = self.old_view
        self.manager._active_messages[1] = (10, 20)

    @override
    async def asyncTearDown(self) -> None:
        await self.manager.cleanup()

    async def test_successful_delete_does_not_schedule_retry(self) -> None:
        await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)

        self.message.delete.assert_awaited_once_with()
        self.assertEqual(self.manager._message_delete_tasks, {})
        self.assertEqual(self.manager._active_messages, {})

    async def test_missing_message_finishes_without_retry(self) -> None:
        self.message.delete.side_effect = discord.NotFound(
            MagicMock(status=404, reason="Not Found"), "missing"
        )

        await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)

        self.message.delete.assert_awaited_once_with()
        self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_forbidden_delete_is_logged_without_retry(self) -> None:
        self.message.delete.side_effect = discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "denied"
        )

        with self.assertLogs(controller_module.logger, level="WARNING") as captured:
            await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)

        self.message.delete.assert_awaited_once_with()
        self.assertEqual(self.manager._message_delete_tasks, {})
        self.assertIn("abandoned", captured.output[0])

    async def test_deterministic_http_errors_do_not_retry(self) -> None:
        for status in (400, 401, 405):
            with self.subTest(status=status):
                self.message.delete.reset_mock()
                self.message.delete.side_effect = discord.HTTPException(
                    MagicMock(status=status, reason="Rejected"), "rejected"
                )

                with self.assertLogs(controller_module.logger, level="WARNING"):
                    await self.manager._safe_delete_message(10, 20)

                self.message.delete.assert_awaited_once_with()
                self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_transient_delete_retries_then_releases_task(self) -> None:
        errors = (
            aiohttp.ClientConnectionError("offline"),
            TimeoutError(),
            discord.HTTPException(MagicMock(status=429, reason="Limited"), "limited"),
            discord.HTTPException(MagicMock(status=500, reason="Failure"), "failure"),
            discord.HTTPException(MagicMock(status=503, reason="Unavailable"), "down"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.message.delete.reset_mock()
                self.message.delete.side_effect = (error, None)
                sleep = AsyncMock()

                with patch.object(asyncio, "sleep", sleep):
                    await self.manager._safe_delete_message(10, 20)
                    task = self.manager._message_delete_tasks[(10, 20)]
                    await task

                self.assertEqual(self.message.delete.await_count, 2)
                sleep.assert_awaited_once_with(2.0)
                self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_transient_channel_fetch_is_retried(self) -> None:
        self.bot.get_channel.return_value = None
        self.bot.fetch_channel.side_effect = (
            aiohttp.ClientConnectionError("offline"),
            self.channel,
        )

        with patch.object(asyncio, "sleep", AsyncMock()):
            await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)
            await self.manager._message_delete_tasks[(10, 20)]

        self.assertEqual(self.bot.fetch_channel.await_count, 2)
        self.message.delete.assert_awaited_once_with()
        self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_retry_stops_when_message_is_missing_or_delete_is_terminal(
        self,
    ) -> None:
        errors = (
            discord.NotFound(MagicMock(status=404, reason="Missing"), "missing"),
            discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "denied"),
            discord.HTTPException(MagicMock(status=400, reason="Bad Request"), "bad"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.message.delete.reset_mock()
                self.message.delete.side_effect = (TimeoutError(), error)
                sleep = AsyncMock()

                with patch.object(asyncio, "sleep", sleep):
                    await self.manager._safe_delete_message(10, 20)
                    await self.manager._message_delete_tasks[(10, 20)]

                self.assertEqual(self.message.delete.await_count, 2)
                sleep.assert_awaited_once_with(2.0)
                self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_persistent_transient_failure_exhausts_bounded_attempts(self) -> None:
        self.message.delete.side_effect = TimeoutError()
        sleep = AsyncMock()

        with (
            patch.object(asyncio, "sleep", sleep),
            self.assertLogs(controller_module.logger, level="WARNING") as captured,
        ):
            await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)
            await self.manager._message_delete_tasks[(10, 20)]

        self.assertEqual(self.message.delete.await_count, 6)
        self.assertEqual(
            sleep.await_args_list,
            [call(2.0), call(10.0), call(30.0), call(60.0), call(120.0)],
        )
        self.assertEqual(self.manager._message_delete_tasks, {})
        self.assertIn("abandoned after 6 attempts", captured.output[0])

    async def test_retry_sleep_allows_new_controller_and_ignores_stale_destroy(
        self,
    ) -> None:
        self.message.delete.side_effect = (TimeoutError(), None)
        sleeping = asyncio.Event()
        resume = asyncio.Event()

        async def wait_for_retry(_delay: float) -> None:
            sleeping.set()
            await resume.wait()

        attempt = PlaybackAttempt(2, make_entry("new-track"))
        player = MagicMock(current_attempt=attempt)
        new_message = MagicMock(id=21)
        new_message.channel.id = 10
        self.channel.send = AsyncMock(return_value=new_message)
        new_view = MagicMock(attempt_id=2)

        with (
            patch.object(asyncio, "sleep", wait_for_retry),
            patch.object(
                controller_module, "TrackControllerView", return_value=new_view
            ),
        ):
            async with asyncio.timeout(5):
                await self.manager.destroy_for_guild(
                    1, ControllerDestroyReason.TRACK_END
                )
                task = self.manager._message_delete_tasks[(10, 20)]
                await sleeping.wait()
                self.assertFalse(self.manager._locks[1].locked())

                await self.manager._safe_delete_message(10, 20)
                self.assertIs(self.manager._message_delete_tasks[(10, 20)], task)
                self.message.delete.assert_awaited_once_with()

                await self.manager.create_for_user(
                    guild_id=1,
                    user_id=2,
                    channel=self.channel,
                    player=player,
                    attempt=attempt,
                )
                await self.manager.destroy_for_guild(
                    1, ControllerDestroyReason.STALE_VIEW, requesting_view=self.old_view
                )
                await self.manager.destroy_for_guild(
                    1, ControllerDestroyReason.TRACK_END, expected_attempt_id=1
                )
                self.assertIs(self.manager.controllers[1], new_view)
                self.assertEqual(self.manager._active_messages[1], (10, 21))
                resume.set()
                await task

        self.assertIs(self.manager.controllers[1], new_view)
        self.assertEqual(self.manager._active_messages[1], (10, 21))
        self.assertEqual(
            self.channel.get_partial_message.call_args_list, [call(20)] * 2
        )
        new_view.stop.assert_not_called()
        self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_cleanup_cancels_sleeping_retry_and_releases_registry(self) -> None:
        self.message.delete.side_effect = TimeoutError()
        sleeping = asyncio.Event()

        async def wait_forever(_delay: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        with patch.object(asyncio, "sleep", wait_forever):
            await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)
            task = self.manager._message_delete_tasks[(10, 20)]
            await sleeping.wait()
            await self.manager.cleanup()

        self.assertTrue(task.cancelled())
        self.assertEqual(self.manager._message_delete_tasks, {})
        self.message.delete.assert_awaited_once_with()
        await self.manager._safe_delete_message(10, 20)
        self.message.delete.assert_awaited_once_with()

    async def test_cleanup_before_retry_starts_releases_registry(self) -> None:
        self.message.delete.side_effect = TimeoutError()
        await self.manager._safe_delete_message(10, 20)
        task = self.manager._message_delete_tasks[(10, 20)]

        await self.manager.cleanup()

        self.assertTrue(task.cancelled())
        self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_cancelled_immediate_delete_propagates_without_retry(self) -> None:
        self.message.delete.side_effect = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.manager.destroy_for_guild(1, ControllerDestroyReason.TRACK_END)

        self.assertEqual(self.manager._message_delete_tasks, {})

    async def test_inflight_failure_cannot_schedule_retry_after_cleanup(self) -> None:
        deleting = asyncio.Event()
        resume = asyncio.Event()

        async def fail_after_cleanup() -> None:
            deleting.set()
            await resume.wait()
            raise TimeoutError

        self.message.delete.side_effect = fail_after_cleanup
        async with asyncio.TaskGroup() as group:
            group.create_task(self.manager._safe_delete_message(10, 20))
            await deleting.wait()
            await self.manager.cleanup()
            resume.set()

        self.assertEqual(self.manager._message_delete_tasks, {})
        self.message.delete.assert_awaited_once_with()

    async def test_unexpected_retry_error_is_retrieved_logged_and_stopped(self) -> None:
        self.message.delete.side_effect = (TimeoutError(), RuntimeError("bug"))
        finished = asyncio.Event()

        with (
            patch.object(asyncio, "sleep", AsyncMock()),
            self.assertLogs(controller_module.logger, level="ERROR") as captured,
        ):
            await self.manager._safe_delete_message(10, 20)
            task = self.manager._message_delete_tasks[(10, 20)]
            task.add_done_callback(lambda _task: finished.set())
            await finished.wait()

        self.assertEqual(len(captured.records), 1)
        self.assertIsNotNone(captured.records[0].exc_info)
        self.assertEqual(self.message.delete.await_count, 2)
        self.assertEqual(self.manager._message_delete_tasks, {})


class TestTrackControllerView(unittest.IsolatedAsyncioTestCase):
    def _make_view(
        self,
        attempt: PlaybackAttempt,
        player: MagicMock | None = None,
        *,
        on_stop: AsyncMock | None = None,
    ) -> tuple[TrackControllerView, MagicMock, AsyncMock]:
        if player is None:
            player = MagicMock(current_attempt=attempt, paused=False, position=0)
        player.seek_attempt = AsyncMock(return_value=True)
        player.toggle_pause_for_attempt = AsyncMock(return_value=True)
        player.skip = AsyncMock(return_value=(attempt, None))
        stop_callback = on_stop or AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=stop_callback,
            on_player_failure=AsyncMock(),
        )
        return view, player, stop_callback

    def _button(
        self, view: TrackControllerView, custom_id: str
    ) -> ui.Item[TrackControllerView]:
        button = next(
            child
            for child in view.children
            if isinstance(child, ui.Button) and child.custom_id == custom_id
        )
        return button

    def test_view_stores_exact_playback_attempt(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, _, _ = self._make_view(attempt)

        self.assertIs(view.attempt, attempt)
        self.assertEqual(view.attempt_id, attempt.attempt_id)

    async def test_skip_calls_player_and_stops_controller_silently(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        on_stop = AsyncMock()
        view, player, _ = self._make_view(
            attempt,
            MagicMock(current_attempt=attempt),
            on_stop=on_stop,
        )
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ) as acknowledge,
            patch(
                "cogs.music.views.controller.send_warning", new=AsyncMock()
            ) as send_warning,
        ):
            skip_button = self._button(view, "btn_skip")
            callback = skip_button.callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        acknowledge.assert_awaited_once_with(interaction)
        player.skip.assert_awaited_once_with(expected=attempt)
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.SKIP)
        send_warning.assert_not_awaited()
        self.assertTrue(view.is_finished())

    async def test_unauthorized_interaction_warns_without_ack_or_operation(
        self,
    ) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, player, _ = self._make_view(attempt)
        interaction = MagicMock()
        interaction.user.id = 99

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ) as acknowledge,
            patch.object(FeedbackUI, "send", new=AsyncMock()) as send,
        ):
            callback = self._button(view, "btn_restart").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        send.assert_awaited_once_with(
            interaction,
            feedback_type=FeedbackType.WARNING,
            description="Это не ваш контроллер.",
            ephemeral=True,
            disable_report_btn=True,
        )
        acknowledge.assert_not_awaited()
        player.seek_attempt.assert_not_awaited()

    async def test_owner_denial_http_error_is_best_effort(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, player, _ = self._make_view(attempt)
        interaction = MagicMock()
        interaction.user.id = 99
        response = MagicMock(status=500, reason="test")
        error = discord.HTTPException(response, "failed")

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ) as acknowledge,
            patch.object(FeedbackUI, "send", new=AsyncMock(side_effect=error)),
            patch("cogs.music.views.controller.logger.debug") as debug,
        ):
            callback = self._button(view, "btn_restart").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        acknowledge.assert_not_awaited()
        player.seek_attempt.assert_not_awaited()
        debug.assert_called_once()

    async def test_restart_acknowledges_before_player_seek(self) -> None:
        calls: list[str] = []
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)

        async def seek(_attempt: PlaybackAttempt, _position: int) -> bool:
            calls.append("seek")
            return True

        view, player, _ = self._make_view(attempt, player)
        player.seek_attempt = AsyncMock(side_effect=seek)
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        async def acknowledge(_interaction: object) -> None:
            calls.append("ack")

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=acknowledge,
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            restart_button = self._button(view, "btn_restart")
            callback = restart_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        self.assertEqual(calls, ["ack", "seek"])
        player.seek_attempt.assert_awaited_once_with(attempt, 0)

    async def test_seek_buttons_pass_exact_attempt_and_expected_positions(
        self,
    ) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        attempt.entry.track.length = 25_000
        cases = (
            ("btn_restart", 0),
            ("btn_back10", 5_000),
            ("btn_fwd10", 25_000),
        )

        for custom_id, expected_position in cases:
            with self.subTest(custom_id=custom_id):
                player = MagicMock(
                    current_attempt=attempt,
                    current=MagicMock(length=999_000),
                    paused=False,
                    position=15_000,
                )
                view, player, _ = self._make_view(attempt, player)
                safe_update = AsyncMock()
                interaction = MagicMock()
                interaction.user.id = 10

                with (
                    patch(
                        "cogs.music.views.controller.ack_component",
                        new=AsyncMock(),
                    ),
                    patch.object(view, "_safe_update", safe_update),
                ):
                    callback = self._button(view, custom_id).callback
                    self.assertIsNotNone(callback)
                    await callback(interaction)

                player.seek_attempt.assert_awaited_once_with(attempt, expected_position)
                safe_update.assert_awaited_once_with(force=True)

    async def test_rejected_seek_preserves_cache_and_does_not_update(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=True, position=15_000)
        view, player, on_stop = self._make_view(attempt, player)
        player.seek_attempt.return_value = False
        view._is_paused_cache = True
        view._frozen_position = 15_000
        view._pause_start_time = 123.0
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            callback = self._button(view, "btn_back10").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        self.assertEqual(view._frozen_position, 15_000)
        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._pause_start_time, 123.0)
        safe_update.assert_not_awaited()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)
        player.seek.assert_not_called()

    async def test_pause_resume_handles_lavalink_io_error(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False)
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock()
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=on_stop,
            on_player_failure=on_player_failure,
        )
        player.toggle_pause_for_attempt = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("down")
        )
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ),
            patch(
                "cogs.music.views.controller.send_warning", new=AsyncMock()
            ) as send_warning,
        ):
            pause_button = self._button(view, "btn_pause_resume")
            callback = pause_button.callback
            self.assertIsNotNone(callback)
            await callback(cast(Interaction[Client], cast(object, interaction)))

        on_player_failure.assert_awaited_once_with(player)
        player.cleanup.assert_not_called()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()

    async def test_pause_resume_updates_cache_from_guarded_result(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False, position=12_000)
        view, player, _ = self._make_view(attempt, player)
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
            patch("cogs.music.views.controller.time.monotonic", return_value=50.0),
        ):
            callback = self._button(view, "btn_pause_resume").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        player.toggle_pause_for_attempt.assert_awaited_once_with(attempt)
        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._frozen_position, 12_000)
        self.assertEqual(view._pause_start_time, 50.0)
        safe_update.assert_awaited_once_with(force=True)
        player.pause.assert_not_called()
        player.resume.assert_not_called()

        player.toggle_pause_for_attempt.reset_mock(return_value=True)
        player.toggle_pause_for_attempt.return_value = False
        safe_update.reset_mock()

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            await callback(interaction)

        self.assertFalse(view._is_paused_cache)
        self.assertIsNone(view._pause_start_time)
        safe_update.assert_awaited_once_with(force=True)

    async def test_rejected_pause_preserves_cache_and_does_not_update(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock(current_attempt=attempt, paused=False, position=20_000)
        view, player, on_stop = self._make_view(attempt, player)
        player.toggle_pause_for_attempt.return_value = None
        view._is_paused_cache = True
        view._frozen_position = 8_000
        view._pause_start_time = 10.0
        safe_update = AsyncMock()
        interaction = MagicMock()
        interaction.user.id = 10

        with (
            patch(
                "cogs.music.views.controller.ack_component",
                new=AsyncMock(),
            ),
            patch.object(view, "_safe_update", safe_update),
        ):
            callback = self._button(view, "btn_pause_resume").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        self.assertTrue(view._is_paused_cache)
        self.assertEqual(view._frozen_position, 8_000)
        self.assertEqual(view._pause_start_time, 10.0)
        safe_update.assert_not_awaited()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)
        player.pause.assert_not_called()
        player.resume.assert_not_called()

    async def test_rejected_skip_stops_controller_as_stale(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        view, player, on_stop = self._make_view(attempt)
        player.skip.return_value = (None, None)
        interaction = MagicMock()
        interaction.user.id = 10

        with patch(
            "cogs.music.views.controller.ack_component",
            new=AsyncMock(),
        ):
            callback = self._button(view, "btn_skip").callback
            self.assertIsNotNone(callback)
            await callback(interaction)

        player.skip.assert_awaited_once_with(expected=attempt)
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.STALE_VIEW)

    async def test_invalidation_failure_still_stops_and_warns(self) -> None:
        attempt = PlaybackAttempt(1, make_entry("track"))
        player = MagicMock()
        player.cleanup = MagicMock()
        on_stop = AsyncMock()
        on_player_failure = AsyncMock(side_effect=RuntimeError("unexpected"))
        view = TrackControllerView(
            user_id=10,
            player=player,
            guild_id=1,
            attempt=attempt,
            on_stop_callback=on_stop,
            on_player_failure=on_player_failure,
        )
        interaction = MagicMock()

        with (
            patch("cogs.music.views.controller.logger.exception") as log_exception,
            patch(
                "cogs.music.views.controller.send_warning", new=AsyncMock()
            ) as send_warning,
        ):
            await view.handle_player_io_error(interaction)

        on_player_failure.assert_awaited_once_with(player)
        log_exception.assert_called_once()
        on_stop.assert_awaited_once_with(view, ControllerDestroyReason.PLAYER_ERROR)
        send_warning.assert_awaited_once()
        player.cleanup.assert_not_called()
