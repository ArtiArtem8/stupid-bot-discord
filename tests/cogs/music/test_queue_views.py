"""Tests for music queue views."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from api.music import QueueSnapshot, RepeatMode
from cogs.music.views import QueuePaginationAdapter, QueuePaginator
from tests.api.music.helpers import make_entry


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
