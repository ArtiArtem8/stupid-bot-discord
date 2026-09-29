"""Public profile rendering boundary: editable SVG -> prepared raster -> frames."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from time import perf_counter

from defusedxml.ElementTree import fromstring, tostring
from PIL import Image

from api.voice.profile.model import VoiceProfile
from cogs.voice.profile.animation import CardAnimation, Motion
from cogs.voice.profile.avatar import load_avatar
from cogs.voice.profile.clips import load_clips
from cogs.voice.profile.design import (
    ASSETS,
    BoundDesign,
    CardIdentity,
    bind_design,
    property_value,
    style,
)
from cogs.voice.profile.raster import NativeRasterizer

__all__ = ["CardIdentity", "PreparedCard", "SvgProfileRenderer"]


@dataclass(frozen=True, slots=True)
class PreparedCard:
    """Prepared layers reused by PNG fallback and all animation frames."""

    design: BoundDesign
    animation: CardAnimation
    prepare_seconds: float

    def frame(self, phase: float = 0) -> Image.Image:
        return self.animation.frame(phase)

    def png(self, phase: float = 0) -> bytes:
        output = BytesIO()
        self.frame(phase).save(output, "PNG", compress_level=3)
        return output.getvalue()


class SvgProfileRenderer:
    """One layout, same core coordinates for every tier and output format."""

    version = "profile-card-editable-2"

    def __init__(self, template: Path = ASSETS / "profile.svg") -> None:
        self.template = template
        self.raster = NativeRasterizer()

    def close(self) -> None:
        self.raster.close()

    def source_revision(self) -> str:
        """Invalidate caches when designer-owned source files change."""
        digest = hashlib.sha256(self.version.encode())
        for font in sorted(self.raster.font_dir.glob("*.ttf")):
            digest.update(font.name.encode())
            digest.update(font.read_bytes())
        digest.update(self.template.read_bytes())
        for path in sorted(self.template.parent.rglob("*")):
            if path.is_file() and path.suffix in (".svg", ".json", ".png"):
                digest.update(str(path.relative_to(self.template.parent)).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def prepare(self, profile: VoiceProfile, identity: CardIdentity) -> PreparedCard:
        started = perf_counter()
        design = bind_design(profile, identity, self.template, self.raster)
        avatar = None
        static_svg = design.static_svg
        root = fromstring(design.svg)
        image_node = next(
            node for node in root.iter() if node.get("id") == "user-avatar"
        )
        box = design.boxes.get("user-avatar")
        if box is not None:
            try:
                avatar = load_avatar(identity.avatar_bytes, box)
                if avatar is not None:
                    clips = {
                        f"url(#{node.get('id')})"
                        for node in root.iter()
                        if node.tag.rsplit("}", 1)[-1] == "clipPath"
                        and len(node) == 1
                        and node[0].tag.rsplit("}", 1)[-1] in ("circle", "ellipse")
                    }
                    if property_value(image_node, "clip-path", "") not in clips:
                        raise ValueError(
                            "Animated avatar requires a circular or elliptical clip"
                        )
                    # Remove frame zero from the base so transparent later frames
                    # reveal the panel, never leave the previous portrait behind.
                    static_root = fromstring(static_svg)
                    for node in static_root.iter():
                        if node.get("id") == "user-avatar":
                            style(node, "display", "none")
                    static_svg = tostring(static_root)
            except ValueError as error:
                avatar = None
                design = replace(
                    design, warnings=(*design.warnings, f"Avatar kept static: {error}")
                )
        base, stars = self.raster.layers((static_svg, design.stars_svg))
        if base.size != (design.width, design.height) or stars.size != base.size:
            raise RuntimeError("Rasterizer changed the declared canvas")
        shell = next(node for node in root.iter() if node.get("id") == "card-shell")
        radius = float(shell.get("rx", "0"))
        motion = Motion.load(self.template.parent / "motion.json", design.tier.value)
        animation = CardAnimation(
            design,
            base,
            stars,
            motion,
            progress_ratio=profile.progress_ratio,
            corner_radius=radius,
        )
        animation.authored = load_clips(self.template.parent, design)
        animation.avatar = avatar
        return PreparedCard(design, animation, perf_counter() - started)
