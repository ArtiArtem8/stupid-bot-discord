"""Disabled effects preserve base pixels; configured ornaments remain visible."""

import unittest
from dataclasses import replace

from PIL import Image

from api.progression.appearance import LevelTier
from cogs.voice.profile.animation import CardAnimation, Motion
from cogs.voice.profile.design import ASSETS, BoundDesign
from cogs.voice.profile.raster import Box


class TestAnimationPreparation(unittest.TestCase):
    def make_animation(self, motion: Motion) -> tuple[CardAnimation, bytes]:
        base = Image.new("RGBA", (128, 96), (20, 30, 40, 255))
        atlas = Image.new("RGBA", base.size, "white")
        self.addCleanup(base.close)
        self.addCleanup(atlas.close)
        design = BoundDesign(
            svg=b'<svg xmlns="http://www.w3.org/2000/svg"/>',
            static_svg=b"",
            stars_svg=b"",
            boxes={
                "card-shell": Box(0, 0, 128, 96),
                "avatar-outline": Box(16, 24, 24, 24),
                "emblem-area": Box(80, 24, 24, 24),
            },
            stars=(),
            tokens={"bright": "#ffffff", "border": "#ff0000"},
            tier=LevelTier.STARTER,
            width=128,
            height=96,
            warnings=(),
        )
        original = base.tobytes()
        animation = CardAnimation(
            design, base, atlas, motion, progress_ratio=0.5, corner_radius=12
        )
        return animation, original

    def test_disabled_effects_preserve_pixels_at_every_phase(self) -> None:
        for tier in ("starter", "uncommon", "rare"):
            with self.subTest(tier=tier):
                animation, original = self.make_animation(
                    Motion.load(ASSETS / "motion.json", tier)
                )
                for phase in (0, 0.25, 0.5, 0.99, 1):
                    frame = animation.frame(phase)
                    try:
                        self.assertEqual(frame.tobytes(), original)
                    finally:
                        frame.close()

    def test_configured_static_orbits_remain_in_base(self) -> None:
        motion = Motion.load(ASSETS / "motion.json", "starter")
        animation, original = self.make_animation(replace(motion, emblem_orbits=1))
        self.addCleanup(animation.base.close)
        self.assertNotEqual(animation.base.tobytes(), original)
        self.assertEqual(animation.base.getpixel((0, 0)), (20, 30, 40, 255))
