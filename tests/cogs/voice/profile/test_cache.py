"""Bounded admission, immutable caching and cancellation contracts."""

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from cogs.voice.profile import cache as cache_module
from cogs.voice.profile.cache import MediaKey, ProfileMediaCache, RenderedProfile
from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.media import ProfileMedia, RenderBusyError
from tests.cogs.voice.profile.test_media import profile_at


def rendered(data: bytes) -> RenderedProfile:
    return RenderedProfile(
        ProfileMedia(data, "png"), profile_at(), CardIdentity("Name", "Guild")
    )


KEY = MediaKey(1, 2, 0, 3, "UTC", "Name", "Guild", "a", None, "v1")


class TestMediaCache(unittest.IsolatedAsyncioTestCase):
    async def test_detail_cancellation_retains_shared_slot_until_close_drains(
        self,
    ) -> None:
        cache = ProfileMediaCache(jobs=1)
        started, release = asyncio.Event(), asyncio.Event()

        async def detail() -> ProfileMedia:
            started.set()
            await release.wait()
            return ProfileMedia(b"detail", "png")

        waiter = asyncio.create_task(cache.render_detail(detail))
        await started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        build = AsyncMock(return_value=rendered(b"main"))
        with self.assertRaises(RenderBusyError):
            await cache.get(KEY, build)
        with self.assertRaises(RenderBusyError):
            await cache.render_detail(detail)
        build.assert_not_awaited()
        release.set()
        await cache.aclose()

    async def test_details_are_not_cached_and_serialize_with_main_preparation(
        self,
    ) -> None:
        cache = ProfileMediaCache(jobs=2)
        started, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
        detail = AsyncMock(return_value=ProfileMedia(b"detail", "png"))

        async def build() -> RenderedProfile:
            started.set()
            await release.wait()
            detail.assert_not_awaited()
            return rendered(b"main")

        async def reveal() -> ProfileMedia:
            queued.set()
            return await cache.render_detail(detail)

        main = asyncio.create_task(cache.get(KEY, build))
        await started.wait()
        other = asyncio.create_task(reveal())
        await queued.wait()
        with self.assertRaises(RenderBusyError):
            await cache.get(replace(KEY, user_id=3), build)
        release.set()
        await asyncio.gather(main, other)
        await cache.render_detail(detail)
        self.assertEqual(detail.await_count, 2)
        await cache.aclose()

    async def test_identity_bytes_count_towards_cache_budget(self) -> None:
        cache = ProfileMediaCache(byte_limit=5)
        card = replace(
            rendered(b"png"), identity=CardIdentity("Name", "Guild", b"avatar")
        )
        build = AsyncMock(return_value=card)
        await cache.get(KEY, build)
        await cache.get(KEY, build)
        self.assertEqual(build.await_count, 2)
        await cache.aclose()

    async def test_shared_job_survives_waiter_cancellation_and_publishes(self) -> None:
        cache = ProfileMediaCache()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def build() -> RenderedProfile:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return rendered(b"png")

        waiter = asyncio.create_task(cache.get(KEY, build))
        await started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        follower = asyncio.create_task(cache.get(KEY, build))
        release.set()
        self.assertEqual((await follower).media.data, b"png")
        await cache.get(KEY, build)
        self.assertEqual(calls, 1)
        await cache.aclose()

    async def test_queue_limit_and_close_drain_admitted_work(self) -> None:
        cache = ProfileMediaCache(jobs=1)
        started, release = asyncio.Event(), asyncio.Event()

        async def build() -> RenderedProfile:
            started.set()
            await release.wait()
            return rendered(b"png")

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

        async def old() -> RenderedProfile:
            started.set()
            await release.wait()
            return rendered(b"old")

        waiter = asyncio.create_task(cache.get(KEY, old))
        await started.wait()
        cache.invalidate(1)
        build = AsyncMock(return_value=rendered(b"new"))
        new_key = replace(KEY, epoch=1)
        follower = asyncio.create_task(cache.get(new_key, build))
        release.set()
        self.assertEqual((await waiter).media.data, b"old")
        self.assertEqual((await follower).media.data, b"new")
        await cache.get(new_key, build)
        build.assert_awaited_once()
        # A stale result was delivered to its original waiter but never cached.
        probe = AsyncMock(return_value=rendered(b"probe"))
        await cache.get(KEY, probe)
        probe.assert_awaited_once()
        await cache.aclose()

    async def test_entry_bytes_ttl_and_revision_limits(self) -> None:
        for options in ({"entries": 1}, {"byte_limit": 5}):
            with self.subTest(options=options):
                cache = ProfileMediaCache(**options)
                build = AsyncMock(return_value=rendered(b"123"))
                await cache.get(KEY, build)
                await cache.get(replace(KEY, revision="v2"), build)
                await cache.get(KEY, build)
                self.assertEqual(build.await_count, 3)
                await cache.aclose()
        cache = ProfileMediaCache(ttl=10)
        build = AsyncMock(return_value=rendered(b"123"))
        with patch.object(cache_module, "monotonic", return_value=0):
            await cache.get(KEY, build)
        with patch.object(cache_module, "monotonic", return_value=11):
            await cache.get(KEY, build)
        self.assertEqual(build.await_count, 2)
        await cache.aclose()

    async def test_failed_job_can_be_retried(self) -> None:
        cache = ProfileMediaCache()
        build = AsyncMock(side_effect=[ValueError("bad"), rendered(b"ok")])
        with self.assertRaises(ValueError):
            await cache.get(KEY, build)
        self.assertEqual((await cache.get(KEY, build)).media.data, b"ok")
        await cache.aclose()

    async def test_new_generation_prevents_old_job_publication(self) -> None:
        cache = ProfileMediaCache()
        started, release = asyncio.Event(), asyncio.Event()

        async def old() -> RenderedProfile:
            started.set()
            await release.wait()
            return rendered(b"old")

        first = asyncio.create_task(cache.get(KEY, old))
        await started.wait()
        new_entered = asyncio.Event()
        build = AsyncMock(return_value=rendered(b"new"))

        async def current() -> RenderedProfile:
            new_entered.set()
            return await cache.get(replace(KEY, generation=4), build)

        second = asyncio.create_task(current())
        await new_entered.wait()
        release.set()
        await first
        await second
        probe = AsyncMock(return_value=rendered(b"probe"))
        await cache.get(KEY, probe)
        probe.assert_awaited_once()
        await cache.aclose()
