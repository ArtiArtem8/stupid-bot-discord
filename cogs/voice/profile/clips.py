"""Optional authored PNG sequences attached to an SVG anchor, not a second layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from cogs.voice.profile.design import BoundDesign, json_object
from cogs.voice.profile.raster import Box
from utils.json_types import JsonObject
from utils.json_utils import get_json


@dataclass(frozen=True, slots=True)
class AuthoredClip:
    frames: tuple[Image.Image, ...]
    x: int
    y: int

    def at(self, phase: float) -> Image.Image:
        # These are discrete authored frames. The last-to-first transition is
        # deliberately preserved; the importer cannot invent an artist's loop.
        return self.frames[min(len(self.frames) - 1, int(phase * len(self.frames)))]


def load_clips(directory: Path, design: BoundDesign) -> tuple[AuthoredClip, ...]:
    """Load approved anchored PNG sequences with bounded decoded memory."""
    manifest = directory / "clips.json"
    if not manifest.exists():
        return ()
    raw = json_object(get_json(manifest))
    entries = json_object(raw).get("clips", [])
    if not isinstance(entries, list) or len(entries) > 8:
        raise ValueError("clips.json supports at most eight authored clips")
    clips: list[AuthoredClip] = []
    memory = 0
    for entry in entries:
        settings = json_object(entry)
        if settings.get("tier") != design.tier.value:
            continue
        files, anchor, size = _clip_source(directory, design, settings)
        memory += size[0] * size[1] * 4 * len(files)
        if memory > 24 * 1024 * 1024:
            raise ValueError("Authored clips exceed their 24 MiB decoded budget")
        frames = _clip_frames(files, size)
        cx, cy = anchor.center
        clips.append(
            AuthoredClip(
                tuple(frames), round(cx - size[0] / 2), round(cy - size[1] / 2)
            )
        )
    return tuple(clips)


def _clip_source(
    directory: Path, design: BoundDesign, settings: JsonObject
) -> tuple[list[Path], Box, tuple[int, int]]:
    anchor_id = settings.get("anchor")
    folder = settings.get("directory")
    if not isinstance(anchor_id, str) or not isinstance(folder, str):
        raise ValueError("A clip needs a string anchor and directory")
    if anchor_id not in design.boxes:
        raise ValueError(f"Missing clip anchor: {anchor_id}")
    path = (directory / folder).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Clip directory must remain inside card assets")
    files = sorted(path.glob("*.png"))
    if not 2 <= len(files) <= 80:
        raise ValueError("An authored clip needs 2..80 equally-timed PNG frames")
    anchor = design.boxes[anchor_id]
    scale = settings.get("scale", 1)
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (float, int))
        or not 0.1 <= scale <= 3
    ):
        raise ValueError("Clip scale must be in [.1,3]")
    size = (
        max(1, round(anchor.width * scale)),
        max(1, round(anchor.height * scale)),
    )
    return files, anchor, size


def _clip_frames(files: list[Path], size: tuple[int, int]) -> list[Image.Image]:
    frames: list[Image.Image] = []
    dimensions = None
    for image_path in files:
        if image_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Clip frame exceeds 2 MiB")
        with Image.open(image_path) as image:
            if image.width * image.height > 1_048_576:
                raise ValueError(
                    "Clip source frames must be no larger than one megapixel"
                )
            if dimensions is not None and image.size != dimensions:
                raise ValueError("All clip frames must share a canvas")
            dimensions = image.size
            frames.append(image.convert("RGBA").resize(size, Image.Resampling.LANCZOS))
    return frames
