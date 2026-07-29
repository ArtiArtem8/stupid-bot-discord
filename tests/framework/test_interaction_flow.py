"""Tests for interaction acknowledgement flow helpers."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from framework import ack_component, run_with_defer

ASYNC_TEST_TIMEOUT = 2.0


def _make_interaction(*, responded: bool = False) -> MagicMock:
    response = MagicMock(spec=discord.InteractionResponse)
    response.is_done.return_value = responded
    response.defer = AsyncMock()

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = response
    return interaction


def _make_http_error() -> discord.HTTPException:
    response = MagicMock(status=500, reason="test")
    return discord.HTTPException(response, "defer failed")


async def _wait_for_event(event: asyncio.Event) -> None:
    """Wait for a synchronization point without allowing a test to hang."""
    await asyncio.wait_for(
        event.wait(),
        timeout=ASYNC_TEST_TIMEOUT,
    )


async def _await_task[T](task: asyncio.Task[T]) -> T:
    """Await a test-owned task with a failure watchdog."""
    return await asyncio.wait_for(
        task,
        timeout=ASYNC_TEST_TIMEOUT,
    )


class TestRunWithDefer(unittest.IsolatedAsyncioTestCase):
    async def _assert_slow_success(self, *, ephemeral: bool) -> None:
        interaction = _make_interaction()
        release = asyncio.Event()
        defer_completed = asyncio.Event()

        async def defer(**_kwargs: object) -> None:
            defer_completed.set()

        interaction.response.defer.side_effect = defer

        async def operation() -> str:
            await release.wait()
            return "ok"

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
                ephemeral=ephemeral,
            )
        )

        await _wait_for_event(defer_completed)
        release.set()

        self.assertEqual(await _await_task(flow), "ok")
        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=ephemeral,
        )

    async def _assert_fast_exception_without_defer(
        self,
        error: Exception,
    ) -> None:
        interaction = _make_interaction()

        async def operation() -> None:
            raise error

        with self.assertRaises(type(error)) as raised:
            await run_with_defer(
                interaction,
                operation(),
                defer_after=1.0,
            )

        self.assertIs(raised.exception, error)
        interaction.response.defer.assert_not_awaited()

    async def _assert_expected_defer_failure_preserves_result(
        self,
        interaction: MagicMock,
        error: Exception,
    ) -> None:
        release = asyncio.Event()
        defer_attempted = asyncio.Event()

        async def defer(**_kwargs: object) -> None:
            defer_attempted.set()
            raise error

        interaction.response.defer.side_effect = defer

        async def operation() -> str:
            await release.wait()
            return "ok"

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        await _wait_for_event(defer_attempted)
        release.set()

        self.assertEqual(await _await_task(flow), "ok")
        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=False,
        )

    async def test_fast_success_returns_result_without_defer(self) -> None:
        interaction = _make_interaction()

        async def operation() -> str:
            return "ok"

        result = await run_with_defer(
            interaction,
            operation(),
            defer_after=1.0,
        )

        self.assertEqual(result, "ok")
        interaction.response.defer.assert_not_awaited()

    async def test_slow_public_success_defers_and_returns_result(self) -> None:
        await self._assert_slow_success(ephemeral=False)

    async def test_slow_private_success_defers_ephemerally(self) -> None:
        await self._assert_slow_success(ephemeral=True)

    async def test_fast_exception_preserves_exception_without_defer(self) -> None:
        await self._assert_fast_exception_without_defer(
            RuntimeError("operation failed")
        )

    async def test_operation_timeout_error_is_not_ux_timeout(self) -> None:
        await self._assert_fast_exception_without_defer(TimeoutError("service timeout"))

    async def test_slow_exception_defers_and_preserves_exception(self) -> None:
        interaction = _make_interaction()
        release = asyncio.Event()
        defer_completed = asyncio.Event()
        error = RuntimeError("operation failed")

        async def defer(**_kwargs: object) -> None:
            defer_completed.set()

        interaction.response.defer.side_effect = defer

        async def operation() -> None:
            await release.wait()
            raise error

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        await _wait_for_event(defer_completed)
        release.set()

        with self.assertRaises(RuntimeError) as raised:
            await _await_task(flow)

        self.assertIs(raised.exception, error)
        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=False,
        )

    async def test_defer_threshold_does_not_cancel_operation(self) -> None:
        interaction = _make_interaction()
        operation_started = asyncio.Event()
        release = asyncio.Event()
        operation_cancelled = asyncio.Event()
        defer_completed = asyncio.Event()

        async def defer(**_kwargs: object) -> None:
            defer_completed.set()

        interaction.response.defer.side_effect = defer

        async def operation() -> str:
            operation_started.set()

            try:
                await release.wait()
            except asyncio.CancelledError:
                operation_cancelled.set()
                raise

            return "complete"

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        await _wait_for_event(operation_started)
        await _wait_for_event(defer_completed)

        self.assertFalse(flow.done())
        self.assertFalse(operation_cancelled.is_set())

        release.set()

        self.assertEqual(await _await_task(flow), "complete")
        self.assertFalse(operation_cancelled.is_set())

    async def test_caller_cancellation_before_defer_cancels_and_joins_operation(
        self,
    ) -> None:
        interaction = _make_interaction()
        operation_started = asyncio.Event()
        operation_cancelled = asyncio.Event()
        operation_finalized = asyncio.Event()

        async def operation() -> None:
            operation_started.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                operation_cancelled.set()
                raise
            finally:
                operation_finalized.set()

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=60.0,
            )
        )

        await _wait_for_event(operation_started)
        flow.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await _await_task(flow)

        self.assertTrue(operation_cancelled.is_set())
        self.assertTrue(operation_finalized.is_set())
        interaction.response.defer.assert_not_awaited()

    async def test_caller_cancellation_during_defer_cancels_and_joins_operation(
        self,
    ) -> None:
        interaction = _make_interaction()
        defer_started = asyncio.Event()
        operation_started = asyncio.Event()
        operation_cancelled = asyncio.Event()
        operation_finalized = asyncio.Event()

        async def defer(**_kwargs: object) -> None:
            defer_started.set()
            await _wait_for_event(operation_started)
            await asyncio.Event().wait()

        interaction.response.defer.side_effect = defer

        async def operation() -> None:
            operation_started.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                operation_cancelled.set()
                raise
            finally:
                operation_finalized.set()

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        await _wait_for_event(defer_started)
        await _wait_for_event(operation_started)
        flow.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await _await_task(flow)

        self.assertTrue(operation_cancelled.is_set())
        self.assertTrue(operation_finalized.is_set())
        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=False,
        )

    async def test_already_responded_skips_defer_and_finishes_operation(
        self,
    ) -> None:
        interaction = _make_interaction(responded=True)
        operation_started = asyncio.Event()
        release = asyncio.Event()

        async def operation() -> str:
            operation_started.set()
            await release.wait()
            return "ok"

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        await _wait_for_event(operation_started)
        release.set()

        self.assertEqual(await _await_task(flow), "ok")
        interaction.response.defer.assert_not_awaited()

    async def test_interaction_responded_race_does_not_interrupt_operation(
        self,
    ) -> None:
        interaction = _make_interaction()

        await self._assert_expected_defer_failure_preserves_result(
            interaction,
            discord.InteractionResponded(interaction),
        )

    async def test_http_exception_during_defer_does_not_replace_result(
        self,
    ) -> None:
        await self._assert_expected_defer_failure_preserves_result(
            _make_interaction(),
            _make_http_error(),
        )

    async def test_unexpected_defer_error_cancels_and_joins_operation(
        self,
    ) -> None:
        interaction = _make_interaction()
        operation_started = asyncio.Event()
        operation_cancelled = asyncio.Event()
        operation_finalized = asyncio.Event()
        error = RuntimeError("unexpected defer failure")

        async def defer(**_kwargs: object) -> None:
            await _wait_for_event(operation_started)
            raise error

        interaction.response.defer.side_effect = defer

        async def operation() -> None:
            operation_started.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                operation_cancelled.set()
                raise
            finally:
                operation_finalized.set()

        flow = asyncio.create_task(
            run_with_defer(
                interaction,
                operation(),
                defer_after=0,
            )
        )

        with self.assertRaises(RuntimeError) as raised:
            await _await_task(flow)

        self.assertIs(raised.exception, error)
        self.assertTrue(operation_cancelled.is_set())
        self.assertTrue(operation_finalized.is_set())
        interaction.response.defer.assert_awaited_once_with(
            thinking=True,
            ephemeral=False,
        )


class TestAckComponent(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_without_spinner(self) -> None:
        interaction = _make_interaction()

        await ack_component(interaction)

        interaction.response.defer.assert_awaited_once_with(
            thinking=False,
            ephemeral=False,
        )

    async def test_already_responded_is_noop(self) -> None:
        interaction = _make_interaction(responded=True)

        await ack_component(interaction)

        interaction.response.defer.assert_not_awaited()
