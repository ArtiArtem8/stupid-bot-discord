"""Integration of the music connection owner with Mafic's node pool."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.runtime import connection as connection_module
from stupidbot.features.music.runtime.connection import (
    ConnectionManager,
    prepare_music_pool,
)


class TestPoolLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_music_cog_can_be_loaded_again_after_close(self) -> None:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        volumes = MagicMock(spec=VolumeStore)
        async with bot:
            first = MusicCog(bot, volumes)
            await bot.add_cog(first)
            await bot.remove_cog("MusicCog")
            self.assertIsNone(mafic.pool_for(bot))

            await prepare_music_pool(bot)
            second = MusicCog(bot, volumes)
            await bot.add_cog(second)
            self.assertIsNot(
                first.components.connection.pool, second.components.connection.pool
            )
            await bot.remove_cog("MusicCog")
            self.assertIsNone(mafic.pool_for(bot))

    async def test_active_pool_is_not_taken_over(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.get_cog.return_value = None
        manager = ConnectionManager(bot)
        try:
            with self.assertRaises(mafic.NodePoolAlreadyBound):
                await prepare_music_pool(bot)
            self.assertIs(mafic.pool_for(bot), manager.pool)
        finally:
            await manager.cleanup()

    async def test_failed_pool_close_can_be_retried_before_reload(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.get_cog.return_value = None
        manager = ConnectionManager(bot)
        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.close = AsyncMock(side_effect=[RuntimeError("close failed"), None])
        await manager.pool.add_node(node)
        try:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                await manager.cleanup()
            self.assertIn(node, manager.pool.failed_nodes)
            self.assertIs(mafic.pool_for(bot), manager.pool)
            await prepare_music_pool(bot)
            self.assertIsNone(mafic.pool_for(bot))
            replacement = ConnectionManager(bot)
            self.assertTrue(await replacement.cleanup())
        finally:
            if mafic.pool_for(bot) is manager.pool:
                await manager.pool.close()

    async def test_failed_node_does_not_make_an_active_pool_available_for_reload(
        self,
    ) -> None:
        bot = MagicMock(spec=commands.Bot)
        bot.get_cog.return_value = None
        manager = ConnectionManager(bot)
        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.quiesce = AsyncMock()
        node.close = AsyncMock(side_effect=[RuntimeError("close failed"), None])
        node.players = []
        await manager.pool.add_node(node)
        try:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                await manager.pool.remove_node(node, transfer_players=False)
            self.assertIn(node, manager.pool.failed_nodes)
            with self.assertRaises(mafic.NodePoolAlreadyBound):
                await prepare_music_pool(bot)
            self.assertIs(mafic.pool_for(bot), manager.pool)
            node.close.assert_awaited_once()
        finally:
            await manager.cleanup()

    async def test_late_pool_failure_is_not_logged_when_another_caller_awaits_it(
        self,
    ) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        started, release, waiting, reported = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def fail_close() -> None:
            started.set()
            await release.wait()
            raise RuntimeError("close failed")

        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.close = AsyncMock(side_effect=fail_close)
        await manager.pool.add_node(node)
        try:
            with (
                patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 0.05),
                self.assertLogs(connection_module.logger, level="WARNING") as logs,
            ):
                self.assertFalse(await manager.cleanup())
                await asyncio.wait_for(started.wait(), 2)
                closing = connection_module._pool_close_tasks[manager.pool]
                closing.add_done_callback(lambda _task: reported.set())
                original_wait = asyncio.wait

                async def observe_wait(
                    tasks: tuple[asyncio.Task[None], ...], *, timeout: float
                ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
                    waiting.set()
                    return await original_wait(tasks, timeout=timeout)

                with (
                    patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 2),
                    patch.object(asyncio, "wait", side_effect=observe_wait),
                ):
                    retry = asyncio.create_task(manager.cleanup())
                    try:
                        await asyncio.wait_for(waiting.wait(), 2)
                        release.set()
                        with self.assertRaisesRegex(RuntimeError, "close failed"):
                            await asyncio.wait_for(retry, 5)
                        await asyncio.wait_for(reported.wait(), 2)
                    finally:
                        release.set()
                self.assertFalse(
                    any("Late Lavalink cleanup failed" in line for line in logs.output)
                )
        finally:
            release.set()
            node.close = AsyncMock()
            await manager.cleanup()

    async def test_late_pool_failure_without_a_waiter_is_logged(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        release, reported = asyncio.Event(), asyncio.Event()

        async def fail_close() -> None:
            await release.wait()
            raise RuntimeError("close failed")

        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.close = AsyncMock(side_effect=fail_close)
        await manager.pool.add_node(node)
        try:
            with (
                patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 0.05),
                self.assertLogs(connection_module.logger, level="WARNING") as logs,
            ):
                self.assertFalse(await manager.cleanup())
                closing = connection_module._pool_close_tasks[manager.pool]
                closing.add_done_callback(lambda _task: reported.set())
                release.set()
                await asyncio.wait_for(reported.wait(), 2)
                self.assertEqual(
                    sum("Late Lavalink cleanup failed" in line for line in logs.output),
                    1,
                )
        finally:
            release.set()
            node.close = AsyncMock()
            await manager.cleanup()

    async def test_cancelled_pool_wait_keeps_owned_close_observable(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        started, release, reported = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def wait_for_close() -> None:
            started.set()
            await release.wait()

        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.close = AsyncMock(side_effect=wait_for_close)
        await manager.pool.add_node(node)
        waiter: asyncio.Task[bool] | None = None
        try:
            with patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 10.0):
                waiter = asyncio.create_task(manager.cleanup())
                await asyncio.wait_for(started.wait(), 2)
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(waiter, 2)
                closing = connection_module._pool_close_tasks[manager.pool]
                self.assertIn(closing, connection_module._pending_node_tasks)
                self.assertNotIn(closing, connection_module._pool_close_waiters)
                closing.add_done_callback(lambda _task: reported.set())
                release.set()
                await asyncio.wait_for(reported.wait(), 5)
                self.assertNotIn(closing, connection_module._pending_node_tasks)
                self.assertIsNone(mafic.pool_for(bot))
        finally:
            release.set()
            if waiter is not None:
                await asyncio.gather(waiter, return_exceptions=True)
            await manager.cleanup()

    async def test_shutdown_with_pending_removal_keeps_pool_owned(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        started, release = asyncio.Event(), asyncio.Event()

        async def quiesce() -> None:
            started.set()
            await release.wait()

        node = MagicMock(spec=mafic.Node, label="old", available=False, client=bot)
        node.connect = AsyncMock()
        node.close = AsyncMock()
        node.quiesce = AsyncMock(side_effect=quiesce)
        node.players = []
        await manager.pool.add_node(node)
        removing: asyncio.Task[None] | None = None
        closing: asyncio.Task[bool] | None = None
        try:
            with (
                patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 0.05),
                patch.object(config, "LAVALINK_CLEANUP_GRACE", 0.05),
            ):
                removing = asyncio.create_task(manager.mark_node_unavailable(node))
                await asyncio.wait_for(started.wait(), 2)
                await asyncio.wait_for(removing, 2)
                closing = asyncio.create_task(manager.cleanup())
                done, _ = await asyncio.wait((closing,), timeout=2)
                self.assertIn(closing, done)
                self.assertFalse(closing.result())
                self.assertIs(mafic.pool_for(bot), manager.pool)
                pool_close = connection_module._pool_close_tasks[manager.pool]
                self.assertIn(pool_close, connection_module._pending_node_tasks)
                reported = asyncio.Event()
                pool_close.add_done_callback(lambda _task: reported.set())
                self.assertFalse(await manager.cleanup())
                self.assertIs(
                    connection_module._pool_close_tasks[manager.pool], pool_close
                )
                with self.assertRaises(mafic.NodePoolAlreadyBound):
                    ConnectionManager(bot)
                release.set()
                await asyncio.wait_for(reported.wait(), 5)
                self.assertNotIn(manager.pool, connection_module._pool_close_tasks)
                self.assertNotIn(pool_close, connection_module._pending_node_tasks)
                self.assertIsNone(mafic.pool_for(bot))
                with patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 2.0):
                    self.assertTrue(await asyncio.wait_for(manager.cleanup(), 5))
                    replacement = ConnectionManager(bot)
                    self.assertTrue(await replacement.cleanup())
        finally:
            release.set()
            if removing is not None:
                await asyncio.gather(removing, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await manager.pool.close()
