"""Small closed-path effects over a prepared card, using designer-owned anchors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from defusedxml.ElementTree import fromstring
from PIL import Image, ImageChops, ImageDraw

from cogs.voice.profile.design import BoundDesign, json_object, property_value
from cogs.voice.profile.raster import Box
from utils.json_utils import get_json

if TYPE_CHECKING:
    from cogs.voice.profile.avatar import AnimatedAvatar
    from cogs.voice.profile.clips import AuthoredClip


# Fraction of one back-and-forth cycle between consecutive prismatic packets.
PACKET_PHASE_LAG = 0.045


@dataclass(frozen=True, slots=True)
class Motion:
    stars: bool
    twinkle_cycles: int
    border_copies: int
    border_cycles: int
    border_length: float
    avatar_orbits: int
    emblem_orbits: int
    progress_packet: bool
    packet_cycles: int
    solar_rays: int
    prismatic: bool

    @classmethod
    def load(cls, path: Path, tier: str) -> Motion:
        document = json_object(get_json(path))
        tiers = json_object(document["tiers"])
        values = json_object(tiers[tier])

        def integer(key: str, low: int, high: int) -> int:
            value = values[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise ValueError(f"Invalid motion setting {key}")
            return value

        def flag(key: str) -> bool:
            value = values[key]
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            return value

        length = values["border_length"]
        if (
            isinstance(length, bool)
            or not isinstance(length, (int, float))
            or not 0.01 <= length <= 0.4
        ):
            raise ValueError("Border length must be between .01 and .4")
        return cls(
            flag("stars"),
            integer("twinkle_cycles", 1, 4),
            integer("border_copies", 0, 4),
            integer("border_cycles", 1, 3),
            float(length),
            integer("avatar_orbits", 0, 3),
            integer("emblem_orbits", 0, 3),
            flag("progress_packet"),
            integer("packet_cycles", 1, 3),
            integer("solar_rays", 0, 16),
            flag("prismatic"),
        )


def orbit(
    box: Box, phase: float, offset: float = 0, *, ratio: float = 1
) -> tuple[float, float]:
    """Periodic position, continuous at t=0 and t=1 without first reducing t."""
    angle = math.tau * (phase + offset)
    cx, cy = box.center
    return cx + box.width / 2 * math.cos(angle), cy + box.height / 2 * ratio * math.sin(
        angle
    )


def rounded_path(box: Box, radius: float) -> tuple[tuple[float, float], ...]:
    """Sample a closed rounded rectangle uniformly by distance, not by corner count."""
    r = min(radius, box.width / 2, box.height / 2)
    straight_x, straight_y = box.width - 2 * r, box.height - 2 * r
    arc = math.pi * r / 2
    segments = (straight_x, arc, straight_y, arc, straight_x, arc, straight_y, arc)
    total = sum(segments)
    points: list[tuple[float, float]] = []
    count = max(128, round(total))
    for i in range(count):
        distance = total * i / count
        segment = 0
        while distance >= segments[segment] and segment < 7:
            distance -= segments[segment]
            segment += 1
        if segment == 0:
            x, y = box.x + r + distance, box.y
        elif segment == 2:
            x, y = box.x + box.width, box.y + r + distance
        elif segment == 4:
            x, y = box.x + box.width - r - distance, box.y + box.height
        elif segment == 6:
            x, y = box.x, box.y + box.height - r - distance
        else:
            centers = {
                1: (box.x + box.width - r, box.y + r, -math.pi / 2),
                3: (box.x + box.width - r, box.y + box.height - r, 0),
                5: (box.x + r, box.y + box.height - r, math.pi / 2),
                7: (box.x + r, box.y + r, math.pi),
            }
            cx, cy, start = centers[segment]
            angle = start + distance / r
            x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
        points.append((x, y))
    return tuple(points)


class CardAnimation:
    """Compose frames from a prepared base, effect masks and star sprites."""

    def __init__(
        self,
        design: BoundDesign,
        base: Image.Image,
        atlas: Image.Image,
        motion: Motion,
        *,
        progress_ratio: float,
        corner_radius: float,
    ) -> None:
        self.design = design
        self.base = base
        self.motion = motion
        self.progress_ratio = progress_ratio
        self.authored: tuple[AuthoredClip, ...] = ()
        self.avatar: AnimatedAvatar | None = None
        # Keep effects inside the opaque interior; the base owns antialiased edges.
        self.clip = base.getchannel("A").point([0] * 255 + [255])
        self.colors = [design.tokens["bright"]]
        if motion.prismatic:
            self.colors.extend(("#9687ff", "#ee74d6"))
        self.diamonds: list[Image.Image] = []
        if motion.avatar_orbits or motion.emblem_orbits:
            for color in self.colors:
                sprite = Image.new("RGBA", (32, 32))
                ImageDraw.Draw(sprite).polygon(
                    [(16, 7), (25, 16), (16, 25), (7, 16)], fill=color
                )
                self.diamonds.append(sprite.resize((16, 16), Image.Resampling.LANCZOS))
        self.path: tuple[tuple[float, float], ...] = ()
        self.border_alpha: Image.Image | None = None
        if motion.border_copies:
            shell = design.boxes["card-shell"]
            self.path = rounded_path(
                Box(shell.x + 3, shell.y + 3, shell.width - 6, shell.height - 6),
                max(1, corner_radius - 3),
            )
            border = Image.new("L", (base.width * 2, base.height * 2))
            ImageDraw.Draw(border).line(
                [(round(x * 2), round(y * 2)) for x, y in (*self.path, self.path[0])],
                fill=255,
                width=4,
                joint="curve",
            )
            self.border_alpha = border.resize(base.size, Image.Resampling.LANCZOS)
        self._prepare_ornaments()
        self._protect_content()
        self.star_sprites: list[tuple[Image.Image, int, int, float]] = []
        if motion.stars:
            for star in design.stars:
                x, y = math.floor(star.box.x) - 1, math.floor(star.box.y) - 1
                right = math.ceil(star.box.x + star.box.width) + 1
                bottom = math.ceil(star.box.y + star.box.height) + 1
                self.star_sprites.append(
                    (atlas.crop((x, y, right, bottom)), x, y, star.offset)
                )

    def frame(self, phase: float) -> Image.Image:
        if not math.isfinite(phase) or not 0 <= phase <= 1:
            raise ValueError("Animation phase must be in [0,1]")
        cfg = self.motion
        result = self.base.copy()
        if self.avatar is not None:
            result.alpha_composite(
                self.avatar.at(phase), (self.avatar.x, self.avatar.y)
            )
        if not any(
            (
                cfg.stars,
                cfg.border_copies,
                cfg.avatar_orbits,
                cfg.emblem_orbits,
                cfg.progress_packet,
                cfg.solar_rays,
                self.authored,
            )
        ):
            return result
        overlay = Image.new("RGBA", result.size)
        self._draw_border(overlay, phase)
        self._draw_satellites(overlay, phase)
        if cfg.solar_rays:
            self._draw_solar_rays(overlay, phase)
        if cfg.progress_packet and self.progress_ratio > 0:
            self._draw_progress_packets(overlay, phase)
        if cfg.stars:
            self._draw_stars(overlay, phase)
        for clip in self.authored:
            overlay.alpha_composite(
                clip.at(0 if phase == 1 else phase), (clip.x, clip.y)
            )
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), self.clip))
        result.alpha_composite(overlay)
        return result

    def _draw_progress_packets(self, overlay: Image.Image, phase: float) -> None:
        # Use the measured fill, including the designer's placement and scale.
        fill = self.design.boxes.get("progress-fill")
        if fill is None:
            return
        inset = fill.height / 4
        available = fill.width - 2 * inset
        width = min(28.0 if self.motion.prismatic else 42.0, available * 0.22)
        height = min(fill.height - 2 * inset, width)
        if width <= 1 or height <= 1:
            return
        top = fill.y + (fill.height - height) / 2
        cycle = (0.0 if phase == 1 else phase) * self.motion.packet_cycles
        scale = 4
        # Draw the delayed followers first so the leader stays visible at turns.
        for index in reversed(range(len(self.colors))):
            position = (1 - math.cos(math.tau * (cycle - index * PACKET_PHASE_LAG))) / 2
            x = fill.x + inset + (available - width) * position
            # Quantize before sizing the crop as well as drawing it. Otherwise
            # roundoff at integer boundaries can change the resampling kernel.
            x = round(x * scale) / scale
            left, upper = math.floor(x) - 2, math.floor(top) - 2
            size = (
                math.ceil(x + width) - left + 2,
                math.ceil(top + height) - upper + 2,
            )
            # Supersample only this tiny sprite; retain fractional coordinates
            # to avoid whole-pixel jumps and jagged caps during movement.
            sprite = Image.new("RGBA", (size[0] * scale, size[1] * scale))
            ImageDraw.Draw(sprite).rounded_rectangle(
                (
                    round((x - left) * scale),
                    round((top - upper) * scale),
                    round((x + width - left) * scale) - 1,
                    round((top + height - upper) * scale) - 1,
                ),
                radius=height * scale / 2,
                fill=self.colors[index],
            )
            overlay.alpha_composite(
                sprite.resize(size, Image.Resampling.LANCZOS), (left, upper)
            )

    def _protect_content(self) -> None:
        # Protect readable text and image content even after a designer moves it.
        protect = ImageDraw.Draw(self.clip)

        root = fromstring(self.design.svg)
        round_clips = {
            f"url(#{node.get('id')})"
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "clipPath"
            and len(node) == 1
            and node[0].tag.rsplit("}", 1)[-1] in ("circle", "ellipse")
        }
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] in ("text", "image"):
                box = self.design.boxes.get(node.get("id", ""))
                if box is not None:
                    bounds = (
                        math.floor(box.x) - 2,
                        math.floor(box.y) - 2,
                        math.ceil(box.x + box.width) + 2,
                        math.ceil(box.y + box.height) + 2,
                    )
                    if (
                        node.tag.rsplit("}", 1)[-1] == "image"
                        and property_value(node, "clip-path", "") in round_clips
                    ):
                        # Empty corners of a circular portrait are not content.
                        protect.ellipse(bounds, fill=0)
                    else:
                        protect.rectangle(bounds, fill=0)

    def _prepare_ornaments(self) -> None:
        if not (self.motion.avatar_orbits or self.motion.emblem_orbits):
            return
        ornaments = Image.new("RGBA", (self.base.width * 2, self.base.height * 2))
        pen = ImageDraw.Draw(ornaments)
        for key, number in (
            ("avatar-outline", self.motion.avatar_orbits),
            ("emblem-area", self.motion.emblem_orbits),
        ):
            area = self.design.boxes[key]
            for index in range(number):
                inset = 6 + index * 5
                box = Box(
                    area.x - inset,
                    area.y - inset,
                    area.width + inset * 2,
                    area.height + inset * 2,
                )
                ratio = 0.57 if key == "emblem-area" and self.motion.prismatic else 1
                points = [orbit(box, step / 96, ratio=ratio) for step in range(97)]
                pen.line(
                    [(round(x * 2), round(y * 2)) for x, y in points],
                    fill=self.design.tokens["border"],
                    width=2,
                )
        fixed = ornaments.resize(self.base.size, Image.Resampling.LANCZOS)
        fixed.putalpha(ImageChops.multiply(fixed.getchannel("A"), self.clip))
        self.base = self.base.copy()
        self.base.alpha_composite(fixed)

    def _draw_border(self, overlay: Image.Image, phase: float) -> None:
        if self.border_alpha is None:
            return
        cfg = self.motion
        count = len(self.path)
        for copy_index in range(cfg.border_copies):
            distance = math.floor(count * phase * cfg.border_cycles + 0.5)
            start = (
                distance + math.floor(count * copy_index / cfg.border_copies)
            ) % count
            length = max(2, round(count * cfg.border_length))
            path = [self.path[(start + step) % count] for step in range(length + 1)]
            selection = Image.new("L", overlay.size)
            ImageDraw.Draw(selection).line(path, fill=255, width=8, joint="curve")
            alpha = ImageChops.multiply(selection, self.border_alpha)
            color_layer = Image.new(
                "RGBA", overlay.size, self.colors[copy_index % len(self.colors)]
            )
            color_layer.putalpha(alpha)
            overlay.alpha_composite(color_layer)

    def _draw_satellites(self, overlay: Image.Image, phase: float) -> None:
        cfg = self.motion
        for key, number in (
            ("avatar-outline", cfg.avatar_orbits),
            ("emblem-area", cfg.emblem_orbits),
        ):
            area = self.design.boxes[key]
            for index in range(number):
                inset = 6 + index * 5
                box = Box(
                    area.x - inset,
                    area.y - inset,
                    area.width + inset * 2,
                    area.height + inset * 2,
                )
                ratio = 0.57 if key == "emblem-area" and cfg.prismatic else 1
                direction = -1 if index % 2 else 1
                centers = [
                    (orbit(box, direction * phase, index * 0.27, ratio=ratio), index)
                ]
                if cfg.prismatic:
                    centers.append(
                        (
                            orbit(
                                box, direction * phase, index * 0.27 + 0.5, ratio=ratio
                            ),
                            index + 1,
                        )
                    )
                for (x, y), color_index in centers:
                    sprite = self.diamonds[color_index % len(self.colors)]
                    overlay.alpha_composite(sprite, (round(x) - 8, round(y) - 8))

    def _draw_solar_rays(self, overlay: Image.Image, phase: float) -> None:
        cfg = self.motion
        area = self.design.boxes["emblem-area"]
        cx, cy = area.center
        r = area.width * 0.63
        radius = math.ceil(r + 14)
        rays = Image.new("RGBA", (radius * 4, radius * 4))
        pen = ImageDraw.Draw(rays)
        for index in range(cfg.solar_rays):
            angle = math.tau * (phase + index / cfg.solar_rays)
            outer = r + (11 if index % 3 == 0 else 5)
            pen.line(
                [
                    (
                        round((radius + r * math.cos(angle)) * 2),
                        round((radius + r * math.sin(angle)) * 2),
                    ),
                    (
                        round((radius + outer * math.cos(angle)) * 2),
                        round((radius + outer * math.sin(angle)) * 2),
                    ),
                ],
                fill=self.colors[0],
                width=3,
            )
        overlay.alpha_composite(
            rays.resize((radius * 2, radius * 2), Image.Resampling.LANCZOS),
            (round(cx) - radius, round(cy) - radius),
        )

    def _draw_stars(self, overlay: Image.Image, phase: float) -> None:
        cfg = self.motion
        for sprite, x, y, offset in self.star_sprites:
            signal = math.cos(math.tau * (phase * cfg.twinkle_cycles + offset))
            if signal > -0.15:
                overlay.alpha_composite(sprite, (x, y))
