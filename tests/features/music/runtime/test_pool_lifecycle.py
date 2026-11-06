"""Integration of the music connection owner with Mafic's node pool."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
import mafic
from discord.ext import commands

from stupidbot import settings as config
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.errors import NodeNotConnectedError
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.runtime import connection as connection_module
from stupidbot.features.music.runtime.connection import (
    ConnectionManager,
    prepare_music_pool,
)


class TestPoolLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_failed_bootstrap_closes_unregistered_node(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        created: list[mafic.Node[discord.Client]] = []
        closed: list[mafic.Node[discord.Client]] = []

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            created.append(node)
            raise aiohttp.ClientConnectionError("offline")

        async def close(node: mafic.Node[discord.Client]) -> None:
            closed.append(node)

        try:
            with (
                patch.object(mafic.Node, "connect", connect),
                patch.object(mafic.Node, "close", close),
                self.assertRaises(NodeNotConnectedError),
            ):
                await manager.initialize()
            self.assertEqual(closed, created)
            self.assertEqual(len(created), 1)
            self.assertNotIn(created[0], manager.pool.managed_nodes)
        finally:
            await manager.cleanup()

    async def test_failed_bootstrap_close_remains_owned_by_pool(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        created: list[mafic.Node[discord.Client]] = []
        closed: list[mafic.Node[discord.Client]] = []

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            created.append(node)
            raise aiohttp.ClientConnectionError("offline")

        async def close(node: mafic.Node[discord.Client]) -> None:
            closed.append(node)
            if len(closed) == 1:
                raise RuntimeError("close failed")

        with (
            patch.object(mafic.Node, "connect", connect),
            patch.object(mafic.Node, "close", close),
        ):
            try:
                with self.assertRaises(NodeNotConnectedError):
                    await manager.initialize()
                self.assertEqual(len(created), 1)
                self.assertIn(created[0], manager.pool.failed_nodes)
                self.assertIn(created[0], manager.pool.managed_nodes)
            finally:
                await manager.cleanup()
                if created and len(closed) == 1:
                    await created[0].close()

        self.assertEqual(len(closed), 2)
        self.assertIsNone(mafic.pool_for(bot))

    async def test_cancelled_bootstrap_keeps_failed_close_owned(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        connecting = asyncio.Event()
        created: list[mafic.Node[discord.Client]] = []
        closed: list[mafic.Node[discord.Client]] = []

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            created.append(node)
            connecting.set()
            await asyncio.Event().wait()

        async def close(node: mafic.Node[discord.Client]) -> None:
            closed.append(node)
            if len(closed) == 1:
                raise RuntimeError("close failed")

        with (
            patch.object(mafic.Node, "connect", connect),
            patch.object(mafic.Node, "close", close),
        ):
            try:
                initializing = asyncio.create_task(manager.initialize())
                await asyncio.wait_for(connecting.wait(), 2)
                initializing.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(initializing, 2)
                self.assertIn(created[0], manager.pool.failed_nodes)
            finally:
                await manager.cleanup()
                if created and len(closed) == 1:
                    await created[0].close()

        self.assertEqual(len(closed), 2)

    async def test_failed_bootstrap_cleanup_is_retried_before_connecting(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        connected = asyncio.Event()
        created: list[mafic.Node[discord.Client]] = []
        closed: list[mafic.Node[discord.Client]] = []

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            created.append(node)
            if len(created) == 1:
                raise aiohttp.ClientConnectionError("offline")
            self.assertGreaterEqual(len(closed), 2)
            node._available = True
            connected.set()

        async def close(node: mafic.Node[discord.Client]) -> None:
            closed.append(node)
            if len(closed) == 1:
                raise RuntimeError("close failed")

        with (
            patch.object(config, "LAVALINK_CONNECT_RETRY_DELAY", 0.0),
            patch.object(mafic.Node, "connect", connect),
            patch.object(mafic.Node, "close", close),
        ):
            try:
                manager.start_lazy_connect()
                retry = manager._lazy_connect_task
                if retry is None:
                    self.fail("Automatic retry was not started")
                await asyncio.wait_for(connected.wait(), 2)
                await asyncio.wait_for(retry, 2)
                self.assertEqual(len(created), 2)
                self.assertIsNot(created[0], created[1])
                self.assertNotIn(created[0], manager.pool.managed_nodes)
                self.assertFalse(manager.pool.failed_nodes)
                self.assertIs(
                    manager.pool.label_to_node[config.LAVALINK_NODE_LABEL], created[1]
                )
            finally:
                await manager.cleanup()

    async def test_cancelled_node_removal_retries_without_reusing_old_node(
        self,
    ) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        old = MagicMock(
            spec=mafic.Node,
            label=config.LAVALINK_NODE_LABEL,
            available=False,
            client=bot,
            players=(),
        )
        old.connect = AsyncMock()
        old.quiesce = AsyncMock(side_effect=asyncio.CancelledError())
        old.close = AsyncMock()
        await manager.pool.add_node(old)
        connected = asyncio.Event()
        replacements: list[mafic.Node[discord.Client]] = []

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            self.assertGreaterEqual(old.close.await_count, 1)
            self.assertNotIn(old, manager.pool.managed_nodes)
            node._available = True
            replacements.append(node)
            connected.set()

        try:
            with (
                patch.object(config, "LAVALINK_CONNECT_RETRY_DELAY", 0.0),
                patch.object(mafic.Node, "connect", connect),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await manager.handle_node_unavailable(old)
                await asyncio.wait_for(connected.wait(), 2)
                retry = manager._lazy_connect_task
                if retry is None:
                    self.fail("Automatic retry was not started")
                await asyncio.wait_for(retry, 2)
            self.assertEqual(len(replacements), 1)
            self.assertIsNot(replacements[0], old)
            self.assertIs(manager.pool.label_to_node[old.label], replacements[0])
        finally:
            await manager.cleanup()

    async def test_failed_node_cleanup_finishes_before_automatic_replacement(
        self,
    ) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        old = MagicMock(
            spec=mafic.Node,
            label=config.LAVALINK_NODE_LABEL,
            available=False,
            client=bot,
            players=(),
        )
        old.connect = AsyncMock()
        old.quiesce = AsyncMock(side_effect=[RuntimeError("quiesce failed"), None])
        closing, release, connected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        close_attempts = 0
        replacements: list[mafic.Node[discord.Client]] = []

        async def close_old() -> None:
            nonlocal close_attempts
            close_attempts += 1
            if close_attempts <= 2:
                raise RuntimeError("close failed")
            closing.set()
            await release.wait()

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            node._available = True
            replacements.append(node)
            connected.set()

        old.close = AsyncMock(side_effect=close_old)
        await manager.pool.add_node(old)
        cleanup_started = asyncio.create_task(closing.wait())
        replacement_started = asyncio.create_task(connected.wait())
        handling: asyncio.Task[set[int]] | None = None
        try:
            with (
                patch.object(config, "LAVALINK_CONNECT_RETRY_DELAY", 0.0),
                patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 5.0),
                patch.object(mafic.Node, "connect", connect),
            ):
                handling = asyncio.create_task(manager.handle_node_unavailable(old))
                done, _ = await asyncio.wait(
                    (cleanup_started, replacement_started),
                    timeout=2,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                self.assertIn(cleanup_started, done)
                self.assertFalse(replacement_started.done())
                release.set()
                await asyncio.wait_for(handling, 2)
                await asyncio.wait_for(replacement_started, 2)
                retry = manager._lazy_connect_task
                if retry is None:
                    self.fail("Automatic retry was not started")
                await asyncio.wait_for(retry, 2)

            self.assertEqual(len(replacements), 1)
            self.assertIsNot(replacements[0], old)
            self.assertNotIn(old, manager.pool.managed_nodes)
            self.assertIs(manager.pool.label_to_node[old.label], replacements[0])
        finally:
            release.set()
            for task in (cleanup_started, replacement_started):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                cleanup_started, replacement_started, return_exceptions=True
            )
            if handling is not None:
                await asyncio.gather(handling, return_exceptions=True)
            await manager.cleanup()

    async def test_pending_node_cleanup_delays_automatic_replacement(self) -> None:
        bot = MagicMock(spec=commands.Bot)
        manager = ConnectionManager(bot)
        old = MagicMock(
            spec=mafic.Node,
            label=config.LAVALINK_NODE_LABEL,
            available=False,
            client=bot,
            players=(),
        )
        old.connect = AsyncMock()
        old.close = AsyncMock()
        started, release, attempted, connected = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def quiesce() -> None:
            started.set()
            await release.wait()

        async def connect(node: mafic.Node[discord.Client], **_kwargs: object) -> None:
            node._available = True
            connected.set()

        initialize = manager.initialize

        async def observe_retry() -> None:
            try:
                await initialize()
            finally:
                attempted.set()

        old.quiesce = AsyncMock(side_effect=quiesce)
        await manager.pool.add_node(old)
        try:
            with (
                patch.object(config, "LAVALINK_CONNECT_RETRY_DELAY", 0.01),
                patch.object(config, "LAVALINK_CLEANUP_TIMEOUT", 0.05),
                patch.object(config, "LAVALINK_CLEANUP_GRACE", 0.05),
                patch.object(mafic.Node, "connect", connect),
                patch.object(manager, "initialize", side_effect=observe_retry),
            ):
                handling = asyncio.create_task(manager.handle_node_unavailable(old))
                await asyncio.wait_for(started.wait(), 2)
                await asyncio.wait_for(handling, 2)
                await asyncio.wait_for(attempted.wait(), 2)
                self.assertIn(old, manager.pool.managed_nodes)
                self.assertFalse(connected.is_set())
                release.set()
                await asyncio.wait_for(connected.wait(), 2)
                retry = manager._lazy_connect_task
                if retry is None:
                    self.fail("Automatic retry was not started")
                await asyncio.wait_for(retry, 2)
                self.assertNotIn(old, manager.pool.managed_nodes)
        finally:
            release.set()
            await manager.cleanup()

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
