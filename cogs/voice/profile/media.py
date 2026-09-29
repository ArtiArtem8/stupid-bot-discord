"""PNG and lossless WebP delivery, with bounded work and explicit PNG fallback."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from io import BytesIO
from time import perf_counter
from typing import Literal

from PIL import features

from api.progression.appearance import LevelTier
from api.voice.profile.model import VoiceProfile
from cogs.voice.profile.svg_renderer import (
    CardIdentity,
    PreparedCard,
    SvgProfileRenderer,
)

logger = logging.getLogger(__name__)
ANIMATED_TIERS = frozenset(
    (
        LevelTier.EPIC,
        LevelTier.MYTHIC,
        LevelTier.LEGENDARY,
        LevelTier.ASCENDANT,
        LevelTier.TRANSCENDENT,
    )
)
MEDIA_LIMIT = 5 * 1024 * 1024


class RenderBusyError(RuntimeError):
    """The bounded rendering queue has no free slots."""


@dataclass(frozen=True, slots=True)
class ProfileMedia:
    """Immutable attachment data; keep the prepared still for upload fallback."""

    data: bytes
    extension: Literal["png", "webp"]
    still_png: bytes = b""

    def within(self, byte_limit: int) -> ProfileMedia:
        if len(self.data) <= byte_limit:
            return self
        if self.still_png and len(self.still_png) <= byte_limit:
            return ProfileMedia(self.still_png, "png")
        raise ValueError("Profile image exceeds the attachment budget")


def encode_webp(prepared: PreparedCard) -> bytes:
    """Encode four seconds at 20 FPS with the approved lossless settings."""
    fps, method = 20, 1
    count = fps * 4
    if prepared.design.width * prepared.design.height * count > 42_000_000:
        raise ValueError("Animation exceeds the 42-megapixel frame buffer budget")
    if not features.check("webp"):
        raise RuntimeError("This Pillow/libwebp build cannot encode animations")
    frames = [prepared.frame(index / count) for index in range(count)]
    output = BytesIO()
    frames[0].save(
        output,
        "WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=1000 // fps,
        loop=0,
        lossless=True,
        quality=75,
        # A four-second sequential card does not need periodic
        # independent keyframes. This is not a lossy or FPS change.
        method=method,
        allow_mixed=False,
        minimize_size=False,
        kmax=0,
    )
    return output.getvalue()


class ProfileMediaRenderer:
    """Own native resources and an uncancellable worker; the cache admits jobs."""

    def __init__(self, *, svg: SvgProfileRenderer | None = None) -> None:
        self.svg = svg
        self._tasks: set[asyncio.Task[ProfileMedia]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[str] | None = None

    def _render_sync(
        self, profile: VoiceProfile, identity: CardIdentity
    ) -> ProfileMedia:
        started = perf_counter()
        if self.svg is None:
            raise RuntimeError("Profile renderer is unavailable")
        prepared = self.svg.prepare(profile, identity)
        png = prepared.png()
        if profile.appearance.tier not in ANIMATED_TIERS:
            return ProfileMedia(png, "png")
        try:
            data = encode_webp(prepared)
            if len(data) > MEDIA_LIMIT:
                raise ValueError("WebP exceeds media budget")
            logger.info(
                "Profile rendered: tier=%s prepare=%.3fs total=%.3fs bytes=%d",
                profile.appearance.tier,
                prepared.prepare_seconds,
                perf_counter() - started,
                len(data),
            )
            return ProfileMedia(data, "webp", png)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Voice profile WebP unavailable; returning prepared PNG")
            logger.debug("WebP failure", exc_info=True)
            return ProfileMedia(png, "png")

    def start(self) -> str:
        """Initialize native resources in a worker thread during Cog load."""
        if self._closed:
            raise RuntimeError("Profile renderer is closed")
        if self.svg is None:
            self.svg = SvgProfileRenderer()
        try:
            self.svg.raster.start()
            return self.svg.source_revision()
        except Exception:
            self.svg.close()
            raise

    async def astart(self) -> str:
        """Own initialization until completion even if Cog loading is cancelled."""
        if self._closed:
            raise RuntimeError("Profile renderer is closed")
        if self._start_task is None:
            self._start_task = asyncio.create_task(asyncio.to_thread(self.start))
        return await asyncio.shield(self._start_task)

    async def render(
        self, profile: VoiceProfile, identity: CardIdentity
    ) -> ProfileMedia:
        if self._closed:
            raise RuntimeError("Profile renderer is closed")
        if self._tasks:
            raise RenderBusyError("The profile renderer is busy; retry shortly")

        async def work() -> ProfileMedia:
            return await asyncio.to_thread(self._render_sync, profile, identity)

        task = asyncio.create_task(work(), name="voice-profile-render")
        self._tasks.add(task)

        def completed(done: asyncio.Task[ProfileMedia]) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()  # consume errors even if the original waiter left

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def aclose(self) -> None:
        """Settle owned jobs and close the native shell, even if a waiter leaves."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        # A running native thread cannot be safely cancelled. Keep ownership and
        # wait for the bounded queue instead of releasing its slot prematurely.
        if self._start_task is not None:
            await asyncio.gather(self._start_task, return_exceptions=True)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self.svg is not None:
            await asyncio.to_thread(self.svg.close)
