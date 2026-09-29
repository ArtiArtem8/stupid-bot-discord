"""Bounded admission, immutable caching and cancellation contracts."""

import asyncio
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, patch

from cogs.voice.profile import cache as cache_module
from cogs.voice.profile.cache import MediaKey, ProfileMediaCache
from cogs.voice.profile.media import ProfileMedia, RenderBusyError

KEY = MediaKey(
    1, 2, 0, 3, 4, date(2026, 9, 29), "UTC", "Name", "Guild", "a", None, "v1"
)


class TestMediaCache(unittest.IsolatedAsyncioTestCase):
    async def test_shared_job_survives_waiter_cancellation_and_publishes(self) -> None:
        cache = ProfileMediaCache()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def build() -> ProfileMedia:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return ProfileMedia(b"png", "png")

        waiter = asyncio.create_task(cache.get(KEY, build))
        await started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        follower = asyncio.create_task(cache.get(KEY, build))
        release.set()
        self.assertEqual((await follower).data, b"png")
        await cache.get(KEY, build)
        self.assertEqual(calls, 1)
        await cache.aclose()

    async def test_queue_limit_and_close_drain_admitted_work(self) -> None:
        cache = ProfileMediaCache(jobs=1)
        started, release = asyncio.Event(), asyncio.Event()

        async def build() -> ProfileMedia:
            started.set()
            await release.wait()
            return ProfileMedia(b"png", "png")

        waiter = asyncio.create_task(cache.get(KEY, build))
        await started.wait()
        with self.assertRaises(RenderBusyError):
            await cache.get(replace(KEY, user_id=3), build)
        closing_started = asyncio.Event()

        async def close() -> None:
            closing_started.set()
            await cache.aclose()

        closing = asyncio.create_task(close())
        await closing_started.wait()
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        with self.assertRaises(RenderBusyError):
            await cache.get(KEY, build)
        release.set()
        await waiter
        await cache.aclose()
        await cache.aclose()

    async def test_old_epoch_does_not_join_or_publish_into_new_cache(self) -> None:
        cache = ProfileMediaCache()
        started, release = asyncio.Event(), asyncio.Event()

        async def old() -> ProfileMedia:
            started.set()
            await release.wait()
            return ProfileMedia(b"old", "png")

        waiter = asyncio.create_task(cache.get(KEY, old))
        await started.wait()
        cache.invalidate(1)
        build = AsyncMock(return_value=ProfileMedia(b"new", "png"))
        new_key = replace(KEY, epoch=1)
        follower = asyncio.create_task(cache.get(new_key, build))
        release.set()
        self.assertEqual((await waiter).data, b"old")
        self.assertEqual((await follower).data, b"new")
        await cache.get(new_key, build)
        build.assert_awaited_once()
        # A stale result was delivered to its original waiter but never cached.
        probe = AsyncMock(return_value=ProfileMedia(b"probe", "png"))
        await cache.get(KEY, probe)
        probe.assert_awaited_once()
        await cache.aclose()

    async def test_entry_bytes_ttl_and_revision_limits(self) -> None:
        for options in ({"entries": 1}, {"byte_limit": 5}):
            with self.subTest(options=options):
                cache = ProfileMediaCache(**options)
                build = AsyncMock(return_value=ProfileMedia(b"123", "png"))
                await cache.get(KEY, build)
                await cache.get(replace(KEY, revision="v2"), build)
                await cache.get(KEY, build)
                self.assertEqual(build.await_count, 3)
                await cache.aclose()
        cache = ProfileMediaCache(ttl=10)
        build = AsyncMock(return_value=ProfileMedia(b"123", "png"))
        with patch.object(cache_module, "monotonic", return_value=0):
            await cache.get(KEY, build)
        with patch.object(cache_module, "monotonic", return_value=11):
            await cache.get(KEY, build)
        self.assertEqual(build.await_count, 2)
        await cache.aclose()

    async def test_failed_job_can_be_retried(self) -> None:
        cache = ProfileMediaCache()
        build = AsyncMock(side_effect=[ValueError("bad"), ProfileMedia(b"ok", "png")])
        with self.assertRaises(ValueError):
            await cache.get(KEY, build)
        self.assertEqual((await cache.get(KEY, build)).data, b"ok")
        await cache.aclose()

    async def test_new_generation_prevents_old_job_publication(self) -> None:
        cache = ProfileMediaCache()
        started, release = asyncio.Event(), asyncio.Event()

        async def old() -> ProfileMedia:
            started.set()
            await release.wait()
            return ProfileMedia(b"old", "png")

        first = asyncio.create_task(cache.get(KEY, old))
        await started.wait()
        new_entered = asyncio.Event()
        build = AsyncMock(return_value=ProfileMedia(b"new", "png"))

        async def current() -> ProfileMedia:
            new_entered.set()
            return await cache.get(replace(KEY, generation=4), build)

        second = asyncio.create_task(current())
        await new_entered.wait()
        release.set()
        await first
        await second
        probe = AsyncMock(return_value=ProfileMedia(b"probe", "png"))
        await cache.get(KEY, probe)
        probe.assert_awaited_once()
        await cache.aclose()
