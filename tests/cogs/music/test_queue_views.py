"""Tests for music queue views."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.music import (
    MusicResult,
    MusicResultStatus,
    QueueEntry,
    QueueSnapshot,
    RepeatMode,
)
from cogs.music.views import (
    QueuePaginationAdapter,
    QueuePaginator,
    QueueUndoView,
)
from cogs.music.views.queue import STALE_QUEUE_REQUEST_MESSAGE
from cogs.music.views.queue import logger as queue_logger
from framework import FeedbackType, FeedbackUI
from tests.api.music.helpers import make_entry


def _make_http_error(
    error_type: type[discord.HTTPException],
    status: int,
) -> discord.HTTPException:
    response = MagicMock(status=status, reason="test")
    return error_type(response, "test failure")


def _make_snapshot(
    *identifiers: str,
    current_identifier: str | None = None,
) -> QueueSnapshot:
    current = (
        make_entry(current_identifier, entry_id=100)
        if current_identifier is not None
        else None
    )
    queue = tuple(
        make_entry(identifier, entry_id=index)
        for index, identifier in enumerate(identifiers, 1)
    )
    return QueueSnapshot(current, queue, RepeatMode.OFF)


class TestQueuePaginator(unittest.IsolatedAsyncioTestCase):
    async def test_first_page_shows_current_and_initial_queue_tracks(self) -> None:
        snapshot = _make_snapshot("one", "two", current_identifier="current")
        adapter = QueuePaginationAdapter(snapshot, page_size=1)

        async def refresh() -> QueueSnapshot | None:
            return snapshot

        paginator = QueuePaginator(adapter, refresh, user_id=42)
        await paginator.prepare()

        embed = paginator.make_embed()

        self.assertEqual(paginator.page, 0)
        self.assertEqual(embed.fields[0].name, "Сейчас играет")
        current_value = embed.fields[0].value
        if not isinstance(current_value, str):
            self.fail("Current track field is missing")
        self.assertIn("Track current", current_value)
        self.assertEqual(embed.fields[1].name, "Далее")
        queue_value = embed.fields[1].value
        if not isinstance(queue_value, str):
            self.fail("Queue field is missing")
        self.assertIn("Track one", queue_value)
        footer_text = embed.footer.text
        if not isinstance(footer_text, str):
            self.fail("Queue footer is missing")
        self.assertIn("Стр. 1/2", footer_text)

    async def test_navigates_across_multiple_pages(self) -> None:
        snapshot = _make_snapshot("one", "two", "three")
        adapter = QueuePaginationAdapter(snapshot, page_size=1)

        async def refresh() -> QueueSnapshot | None:
            return snapshot

        paginator = QueuePaginator(adapter, refresh, user_id=42)
        await paginator.prepare()
        interaction = MagicMock()
        edit_message = AsyncMock()

        self.assertEqual(await paginator.get_total_pages(), 3)

        with patch.object(interaction.response, "edit_message", edit_message):
            await paginator.next_page(interaction)

        self.assertEqual(paginator.page, 1)
        edit_call = edit_message.await_args
        if edit_call is None:
            self.fail("Paginator did not edit the interaction message")
        edited_embed = edit_call.kwargs["embed"]
        queue_value = edited_embed.fields[0].value
        if not isinstance(queue_value, str):
            self.fail("Queue field is missing")
        self.assertIn("Track two", queue_value)

    async def test_refresh_replaces_snapshot_and_returns_to_first_page(self) -> None:
        initial = _make_snapshot("old-one", "old-two")
        refreshed = _make_snapshot("new-one", "new-two")
        adapter = QueuePaginationAdapter(initial, page_size=1)

        async def refresh() -> QueueSnapshot | None:
            return refreshed

        paginator = QueuePaginator(adapter, refresh, user_id=42)
        paginator.page = 1
        interaction = MagicMock()
        edit_message = AsyncMock()

        with patch.object(interaction.response, "edit_message", edit_message):
            await paginator.refresh(interaction)

        self.assertIs(adapter.snapshot, refreshed)
        self.assertEqual(paginator.page, 0)
        edit_call = edit_message.await_args
        if edit_call is None:
            self.fail("Refresh did not edit the interaction message")
        edited_embed = edit_call.kwargs["embed"]
        queue_value = edited_embed.fields[0].value
        if not isinstance(queue_value, str):
            self.fail("Queue field is missing")
        self.assertIn("Track new-one", queue_value)

    async def test_empty_refresh_warns_and_finishes_view(self) -> None:
        snapshot = _make_snapshot("one")
        adapter = QueuePaginationAdapter(snapshot)

        async def refresh() -> QueueSnapshot | None:
            return None

        paginator = QueuePaginator(adapter, refresh, user_id=42)
        interaction = MagicMock()

        with patch(
            "cogs.music.views.queue.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            await paginator.refresh(interaction)

        send_warning.assert_awaited_once_with(
            interaction,
            "Не удалось обновить очередь",
            ephemeral=True,
        )
        self.assertTrue(paginator.is_finished())

    async def test_unauthorized_interaction_gets_private_warning(self) -> None:
        snapshot = _make_snapshot("one")
        adapter = QueuePaginationAdapter(snapshot)

        async def refresh() -> QueueSnapshot | None:
            return snapshot

        paginator = QueuePaginator(adapter, refresh, user_id=42)
        interaction = MagicMock()
        interaction.user.id = 99

        with patch(
            "cogs.music.views.queue.send_warning",
            new=AsyncMock(),
        ) as send_warning:
            allowed = await paginator.interaction_check(interaction)

        self.assertFalse(allowed)
        send_warning.assert_awaited_once_with(
            interaction,
            "Попрошу не трогать, это не ваше сообщение.",
            ephemeral=True,
        )


class TestQueueUndoView(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_before_callback_and_edits_component_message(
        self,
    ) -> None:
        expected = make_entry("expected", requester_id=42)
        events: list[str] = []

        async def acknowledge(_interaction: object) -> None:
            events.append("acknowledge")

        async def remove_entries(
            _guild_id: int,
            _entries: tuple[QueueEntry, ...],
            _requester_id: int,
        ) -> MusicResult[tuple[QueueEntry, ...]]:
            events.append("remove")
            return MusicResult(
                MusicResultStatus.SUCCESS,
                "removed",
                data=(expected,),
            )

        remove = AsyncMock(side_effect=remove_entries)
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message
        success_embed = MagicMock()

        async def edit_message(**_kwargs: object) -> None:
            self.assertTrue(view.is_finished())

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(side_effect=acknowledge),
            ) as acknowledge_mock,
            patch.object(
                FeedbackUI,
                "make_embed",
                return_value=success_embed,
            ) as make_embed,
            patch.object(
                message,
                "edit",
                new=AsyncMock(side_effect=edit_message),
            ) as edit,
        ):
            await view.remove(interaction)

        self.assertEqual(events, ["acknowledge", "remove"])
        acknowledge_mock.assert_awaited_once_with(interaction)
        remove.assert_awaited_once_with(123, (expected,), 42)
        self.assertTrue(view.is_finished())
        edit.assert_awaited_once()
        edit_call = edit.await_args
        if edit_call is None:
            self.fail("Expected original queue feedback to be edited")
        make_embed.assert_called_once_with(
            title="Удалено из очереди",
            description="",
            feedback_type=FeedbackType.SUCCESS,
        )
        self.assertIs(edit_call.kwargs["embed"], success_embed)
        self.assertIsNone(edit_call.kwargs["view"])
        self.assertEqual(edit_call.kwargs["delete_after"], 60)

    async def test_acknowledges_while_remove_callback_is_blocked(self) -> None:
        expected = make_entry("expected", requester_id=42)
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()

        async def remove_entries(
            _guild_id: int,
            _entries: tuple[QueueEntry, ...],
            _requester_id: int,
        ) -> MusicResult[tuple[QueueEntry, ...]]:
            callback_started.set()
            await release_callback.wait()
            return MusicResult(
                MusicResultStatus.SUCCESS,
                "removed",
                data=(expected,),
            )

        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove_entries,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(),
            ) as acknowledge,
            patch.object(message, "edit", new=AsyncMock()),
        ):
            task = asyncio.create_task(view.remove(interaction))
            await callback_started.wait()
            acknowledge.assert_awaited_once_with(interaction)
            self.assertFalse(task.done())
            self.assertFalse(view.is_finished())
            release_callback.set()
            await task

    async def test_playlist_success_reports_removed_track_count(self) -> None:
        expected = (
            make_entry("one", entry_id=1, requester_id=42),
            make_entry("two", entry_id=2, requester_id=42),
            make_entry("three", entry_id=3, requester_id=42),
        )
        removed = expected[1:]
        remove = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "removed",
                data=removed,
            )
        )
        view = QueueUndoView(
            guild_id=123,
            expected_entries=expected,
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message
        success_embed = MagicMock()

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(),
            ),
            patch.object(
                FeedbackUI,
                "make_embed",
                return_value=success_embed,
            ) as make_embed,
            patch.object(message, "edit", new=AsyncMock()) as edit,
        ):
            await view.remove(interaction)

        edit_call = edit.await_args
        if edit_call is None:
            self.fail("Expected original playlist feedback to be edited")
        make_embed.assert_called_once_with(
            title="Удалено из очереди",
            description="Треков: 2",
            feedback_type=FeedbackType.SUCCESS,
        )
        self.assertIs(edit_call.kwargs["embed"], success_embed)
        self.assertIsNone(edit_call.kwargs["view"])
        self.assertEqual(edit_call.kwargs["delete_after"], 60)
        self.assertTrue(view.is_finished())

    async def test_zero_removal_removes_view_and_sends_private_feedback(
        self,
    ) -> None:
        expected = make_entry("expected", requester_id=42)
        remove = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.FAILURE,
                "stale",
            )
        )
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message

        async def edit_message(**_kwargs: object) -> None:
            self.assertTrue(view.is_finished())

        async def send_followup(*_args: object, **_kwargs: object) -> None:
            self.assertTrue(view.is_finished())

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(),
            ),
            patch.object(
                message,
                "edit",
                new=AsyncMock(side_effect=edit_message),
            ) as edit,
            patch.object(
                interaction.followup,
                "send",
                new=AsyncMock(side_effect=send_followup),
            ) as followup,
        ):
            await view.remove(interaction)

        self.assertTrue(view.is_finished())
        edit.assert_awaited_once_with(view=None)
        followup.assert_awaited_once_with(
            STALE_QUEUE_REQUEST_MESSAGE,
            ephemeral=True,
        )

    async def test_missing_component_message_stops_without_callback(self) -> None:
        expected = make_entry("expected", requester_id=42)
        remove = AsyncMock()
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        interaction.message = None

        with patch(
            "cogs.music.views.queue.ack_component",
            new=AsyncMock(),
        ) as acknowledge:
            await view.remove(interaction)

        acknowledge.assert_awaited_once_with(interaction)
        remove.assert_not_awaited()
        self.assertTrue(view.is_finished())

    async def test_on_error_stops_view_and_removes_component(self) -> None:
        expected = make_entry("expected", requester_id=42)
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=AsyncMock(),
            timeout=120,
        )
        interaction = MagicMock()
        message = MagicMock()
        interaction.message = message
        error = RuntimeError("queue undo failed")

        with (
            patch.object(queue_logger, "error") as log_error,
            patch.object(message, "edit", new=AsyncMock()) as edit,
        ):
            await view.on_error(interaction, error, view.remove_button)

        self.assertTrue(view.is_finished())
        log_error.assert_called_once_with(
            "Queue undo interaction failed",
            exc_info=error,
        )
        edit.assert_awaited_once_with(view=None)

    async def test_on_error_ignores_component_cleanup_http_errors(self) -> None:
        cases = (
            (discord.NotFound, 404),
            (discord.Forbidden, 403),
            (discord.HTTPException, 500),
        )

        for error_type, status in cases:
            with self.subTest(error_type=error_type.__name__):
                expected = make_entry("expected", requester_id=42)
                view = QueueUndoView(
                    guild_id=123,
                    expected_entries=(expected,),
                    requester_id=42,
                    remove_callback=AsyncMock(),
                    timeout=120,
                )
                interaction = MagicMock()
                message = MagicMock()
                interaction.message = message
                cleanup_error = _make_http_error(error_type, status)

                with (
                    patch.object(queue_logger, "error"),
                    patch.object(
                        message,
                        "edit",
                        new=AsyncMock(side_effect=cleanup_error),
                    ) as edit,
                ):
                    await view.on_error(
                        interaction,
                        RuntimeError("queue undo failed"),
                        view.remove_button,
                    )

                self.assertTrue(view.is_finished())
                edit.assert_awaited_once_with(view=None)

    async def test_success_edit_error_leaves_view_finished(self) -> None:
        expected = make_entry("expected", requester_id=42)
        remove = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "removed",
                data=(expected,),
            )
        )
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message
        edit_error = _make_http_error(discord.HTTPException, 500)

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(),
            ),
            patch.object(FeedbackUI, "make_embed", return_value=MagicMock()),
            patch.object(
                message,
                "edit",
                new=AsyncMock(side_effect=edit_error),
            ),
            self.assertRaises(discord.HTTPException),
        ):
            await view.remove(interaction)

        self.assertTrue(view.is_finished())
        remove.assert_awaited_once_with(123, (expected,), 42)

    async def test_service_error_is_cleaned_up_by_on_error_without_retry(
        self,
    ) -> None:
        expected = make_entry("expected", requester_id=42)
        service_error = RuntimeError("service failed")
        remove = AsyncMock(side_effect=service_error)
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 42
        message = MagicMock()
        interaction.message = message

        with (
            patch(
                "cogs.music.views.queue.ack_component",
                new=AsyncMock(),
            ),
            self.assertRaises(RuntimeError),
        ):
            await view.remove(interaction)

        self.assertFalse(view.is_finished())

        with (
            patch.object(queue_logger, "error"),
            patch.object(message, "edit", new=AsyncMock()) as edit,
        ):
            await view.on_error(
                interaction,
                service_error,
                view.remove_button,
            )

        self.assertTrue(view.is_finished())
        remove.assert_awaited_once_with(123, (expected,), 42)
        edit.assert_awaited_once_with(view=None)

    async def test_unauthorized_interaction_keeps_view_active(self) -> None:
        expected = make_entry("expected", requester_id=42)
        remove = AsyncMock()
        view = QueueUndoView(
            guild_id=123,
            expected_entries=(expected,),
            requester_id=42,
            remove_callback=remove,
            timeout=120,
        )
        interaction = MagicMock()
        interaction.user.id = 99

        with patch.object(
            interaction.response,
            "send_message",
            new=AsyncMock(),
        ) as send:
            allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertFalse(view.is_finished())
        remove.assert_not_awaited()
        send.assert_awaited_once_with(
            "Удалить из очереди может только автор запроса.",
            ephemeral=True,
        )
