"""Bound immutable media and admitted jobs for one Cog lifetime.

All access runs on the event loop. Admission and task registration have no await
between them; one worker serializes builds across awaits. Cancelled Discord
waiters cannot release a running native job or erase its strong owner.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from cogs.voice.profile.media import ProfileMedia, RenderBusyError


@dataclass(frozen=True, slots=True)
class MediaKey:
    """Pixel inputs plus the persisted snapshot and deployment revision."""

    guild_id: int
    user_id: int
    epoch: int
    generation: int
    timezone: str
    display_name: str
    guild_name: str
    avatar_key: str
    guild_icon_key: str | None
    revision: str


class ProfileMediaCache:
    """Own a bounded single-flight queue, TTL/LRU bytes and orderly draining."""

    def __init__(
        self,
        *,
        entries: int = 32,
        byte_limit: int = 64 * 1024 * 1024,
        jobs: int = 4,
        ttl: float = 300,
    ) -> None:
        if min(entries, byte_limit, jobs, ttl) <= 0:
            raise ValueError("Cache limits must be positive")
        self._entries = entries
        self._byte_limit = byte_limit
        self._jobs = jobs
        self._ttl = ttl
        self._bytes = 0
        self._epoch = 0
        self._generations: dict[int, int] = {}
        self._cache: OrderedDict[MediaKey, tuple[float, ProfileMedia]] = OrderedDict()
        self._tasks: dict[MediaKey, asyncio.Task[ProfileMedia]] = {}
        self._worker = asyncio.Semaphore(1)
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def invalidate(self, epoch: int) -> None:
        """Prevent old-owner jobs from publishing after collector replacement."""
        self._epoch = epoch
        self._cache.clear()
        self._bytes = 0
        self._generations.clear()

    @staticmethod
    def _size(media: ProfileMedia) -> int:
        return len(media.data) + len(media.still_png)

    def _lookup(self, key: MediaKey) -> ProfileMedia | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        created, media = cached
        if monotonic() - created >= self._ttl:
            del self._cache[key]
            self._bytes -= self._size(media)
            return None
        self._cache.move_to_end(key)
        return media

    async def get(
        self,
        key: MediaKey,
        build: Callable[[], Awaitable[ProfileMedia]],
    ) -> ProfileMedia:
        """Share matching jobs; reject excess distinct requests without queuing."""
        if self._closed:
            raise RenderBusyError("Profile runtime is closing")
        if key.epoch == self._epoch:
            self._generations[key.guild_id] = max(
                key.generation, self._generations.get(key.guild_id, 0)
            )
        cached = self._lookup(key)
        if cached is not None:
            return cached
        task = self._tasks.get(key)
        if task is None:
            if len(self._tasks) >= self._jobs:
                raise RenderBusyError("Profile queue is full")
            task = asyncio.create_task(self._produce(key, build))
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._completed(key, done))
        return await asyncio.shield(task)

    def _completed(self, key: MediaKey, task: asyncio.Task[ProfileMedia]) -> None:
        self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _produce(
        self,
        key: MediaKey,
        build: Callable[[], Awaitable[ProfileMedia]],
    ) -> ProfileMedia:
        async with self._worker:
            cached = self._lookup(key)
            if cached is not None:
                return cached
            media = await build()
            size = self._size(media)
            if (
                key.epoch == self._epoch
                and key.generation == self._generations.get(key.guild_id)
                and size <= self._byte_limit
            ):
                self._cache[key] = (monotonic(), media)
                self._bytes += size
                while (
                    len(self._cache) > self._entries or self._bytes > self._byte_limit
                ):
                    _, (_, removed) = self._cache.popitem(last=False)
                    self._bytes -= self._size(removed)
            return media

    async def aclose(self) -> None:
        """Stop admission, drain shared jobs and release cached media."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._cache.clear()
        self._bytes = 0
