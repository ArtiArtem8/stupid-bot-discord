"""Tests for delayed Discord acknowledgement in music interactions."""

import asyncio
import unittest
from typing import cast
from unittest.mock import AsyncMock, patch

from discord import Client, Interaction

from cogs.music.responder import MusicInteractionResponder


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.defer = AsyncMock(side_effect=self._defer)

    def is_done(self) -> bool:
        return self.done

    async def _defer(self, **_kwargs: object) -> None:
        self.done = True


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()


class TestMusicInteractionResponder(unittest.IsolatedAsyncioTestCase):
    async def test_fast_operation_does_not_defer(self) -> None:
        interaction = FakeInteraction()
        responder = MusicInteractionResponder(
            cast(Interaction[Client], cast(object, interaction)), budget=0.1
        )

        result = await responder.await_with_defer_budget(asyncio.sleep(0, result="ok"))

        self.assertEqual(result, "ok")
        interaction.response.defer.assert_not_awaited()

    async def test_slow_operation_defers_public_response(self) -> None:
        interaction = FakeInteraction()
        responder = MusicInteractionResponder(
            cast(Interaction[Client], cast(object, interaction)), budget=0.001
        )

        result = await responder.await_with_defer_budget(
            asyncio.sleep(0.01, result="ok")
        )

        self.assertEqual(result, "ok")
        interaction.response.defer.assert_awaited_once_with(
            thinking=True, ephemeral=False
        )

    async def test_slow_private_operation_selects_ephemeral_at_defer(self) -> None:
        interaction = FakeInteraction()
        responder = MusicInteractionResponder(
            cast(Interaction[Client], cast(object, interaction)), budget=0.001
        )

        await responder.await_with_defer_budget(asyncio.sleep(0.01), ephemeral=True)

        interaction.response.defer.assert_awaited_once_with(
            thinking=True, ephemeral=True
        )

    async def test_preflight_failure_is_ephemeral_without_defer(self) -> None:
        interaction = FakeInteraction()
        responder = MusicInteractionResponder(
            cast(Interaction[Client], cast(object, interaction))
        )

        with patch("cogs.music.responder.FeedbackUI.send", new=AsyncMock()) as send:
            await responder.send_private_failure("Нет voice channel")

        send.assert_awaited_once()
        call = send.await_args
        self.assertIsNotNone(call)
        if call is None:
            self.fail("expected FeedbackUI.send to be awaited")
        self.assertTrue(call.kwargs["ephemeral"])
        interaction.response.defer.assert_not_awaited()
