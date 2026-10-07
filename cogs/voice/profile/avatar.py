"""Bounded animated avatars, retimed to complete whole loops in four seconds."""

from __future__ import annotations

import math
import warnings
from bisect import bisect_right
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import (
    GifImagePlugin,
    Image,
    ImageChops,
    ImageDraw,
    ImageOps,
    UnidentifiedImageError,
)

from cogs.voice.profile.raster import Box

type AvatarMode = Literal["static", "animated"]


@dataclass(frozen=True, slots=True)
class AnimatedAvatar:
    frames: tuple[Image.Image, ...]
    ends_ms: tuple[int, ...]
    repeats: int
    x: int
    y: int

    @property
    def duration_ms(self) -> int:
        return self.ends_ms[-1]

    def at(self, phase: float) -> Image.Image:
        if not math.isfinite(phase) or not 0 <= phase <= 1:
            raise ValueError("Avatar phase must be in [0,1]")
        # Preserve the GIF's relative frame durations, including its own seam.
        elapsed = ((0.0 if phase == 1 else phase) * self.repeats % 1) * self.duration_ms
        index = min(len(self.frames) - 1, bisect_right(self.ends_ms, elapsed))
        return self.frames[index]


def load_avatar(
    data: bytes | None, box: Box, *, mode: AvatarMode = "animated"
) -> AnimatedAvatar | None:
    """Decode the requested GIF frames within byte/pixel budgets.

    Static output only validates and decodes frame zero; later damaged pixels
    do not reject it. Non-GIF and single-frame images retain the SVG path.
    Animated output validates the whole sequence and preserves its timing.
    """
    if data is None:
        return None
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Animated avatar exceeds 4 MiB")
    size = (max(1, round(box.width)), max(1, round(box.height)))
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data)) as source:
                # Only GIF is enabled here; other formats retain their static path.
                if not isinstance(source, GifImagePlugin.GifImageFile):
                    return None
                if size[0] * size[1] * 4 > 8 * 1024 * 1024:
                    raise ValueError("Animated avatar target exceeds 8 MiB")
                frames, ends = _masked_frames(source, size, mode)
        except (
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise ValueError("Unsupported or damaged animated avatar") from error
    if not frames:
        return None
    return AnimatedAvatar(
        tuple(frames),
        tuple(ends),
        max(1, math.floor(4000 / ends[-1] + 0.5)),
        round(box.x),
        round(box.y),
    )


def _masked_frames(
    source: GifImagePlugin.GifImageFile, size: tuple[int, int], mode: AvatarMode
) -> tuple[list[Image.Image], list[int]]:
    with Image.new("L", (size[0] * 4, size[1] * 4)) as large_mask:
        ImageDraw.Draw(large_mask).ellipse(
            (0, 0, large_mask.width - 1, large_mask.height - 1), fill=255
        )
        with large_mask.resize(size, Image.Resampling.LANCZOS) as mask:
            frames, ends = _decode_frames(source, size, mask, mode)
    single_frame = len(frames) < 2
    if mode == "static":
        try:
            # Pillow inspects the next GIF header without decoding its pixels.
            # Keep single-frame GIFs on the existing SVG antialiasing path.
            single_frame = not source.is_animated
        except (OSError, ValueError, EOFError):
            # Frame zero is already valid; damaged later metadata is irrelevant.
            single_frame = False
    if single_frame:
        for frame in frames:
            frame.close()
        return [], []
    return frames, ends


def _decode_frames(
    source: Image.Image, size: tuple[int, int], mask: Image.Image, mode: AvatarMode
) -> tuple[list[Image.Image], list[int]]:
    frames: list[Image.Image] = []
    ends: list[int] = []
    elapsed = 0
    source_pixels = 0
    for index in range(1 if mode == "static" else 201):
        try:
            source.seek(index)
        except EOFError:
            break
        source_pixels += source.width * source.height
        if index == 200 or source_pixels > 32_000_000:
            raise ValueError("Animated avatar exceeds 200 frames or 32 megapixels")
        if (index + 1) * size[0] * size[1] * 4 > 8 * 1024 * 1024:
            raise ValueError("Animated avatar exceeds 8 MiB of decoded frames")
        metadata: dict[str | tuple[int, int], object] = source.info
        duration = metadata.get("duration", 100)
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            duration = 100
        elapsed += duration
        # Sequential seek/convert applies GIF disposal before resizing.
        with source.convert("RGBA") as rgba:
            frame = ImageOps.fit(rgba, size, method=Image.Resampling.LANCZOS)
        frame.putalpha(ImageChops.multiply(frame.getchannel("A"), mask))
        frames.append(frame)
        ends.append(elapsed)
    return frames, ends
