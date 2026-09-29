"""Bounded animated avatars, retimed to complete whole loops in four seconds."""

from __future__ import annotations

import math
import warnings
from bisect import bisect_right
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageOps, UnidentifiedImageError

from cogs.voice.profile.raster import Box


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


def load_avatar(data: bytes | None, box: Box) -> AnimatedAvatar | None:
    """Decode GIF frames within byte/pixel budgets; return None for static input."""
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
                if source.format != "GIF":
                    return None
                if size[0] * size[1] * 4 > 8 * 1024 * 1024:
                    raise ValueError("Animated avatar target exceeds 8 MiB")
                mask = Image.new("L", (size[0] * 4, size[1] * 4))
                ImageDraw.Draw(mask).ellipse(
                    (0, 0, mask.width - 1, mask.height - 1), fill=255
                )
                mask = mask.resize(size, Image.Resampling.LANCZOS)
                frames, ends = _decode_frames(source, size, mask)
        except (
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise ValueError("Unsupported or damaged animated avatar") from error
    if len(frames) < 2:
        return None
    return AnimatedAvatar(
        tuple(frames),
        tuple(ends),
        max(1, math.floor(4000 / ends[-1] + 0.5)),
        round(box.x),
        round(box.y),
    )


def _decode_frames(
    source: Image.Image, size: tuple[int, int], mask: Image.Image
) -> tuple[list[Image.Image], list[int]]:
    frames: list[Image.Image] = []
    ends: list[int] = []
    elapsed = 0
    source_pixels = 0
    for index in range(201):
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
        frame = ImageOps.fit(
            source.convert("RGBA"), size, method=Image.Resampling.LANCZOS
        )
        frame.putalpha(ImageChops.multiply(frame.getchannel("A"), mask))
        frames.append(frame)
        ends.append(elapsed)
    return frames, ends
