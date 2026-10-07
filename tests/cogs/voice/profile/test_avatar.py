"""Static delivery decodes frame zero without weakening its image budgets."""

import unittest
from io import BytesIO
from struct import pack

from PIL import Image

from cogs.voice.profile.avatar import load_avatar
from cogs.voice.profile.raster import Box


def gif(frames: int = 2) -> bytes:
    images = [
        Image.new("RGBA", (16, 16), (255 if i % 2 else 0, 0, 255, 255))
        for i in range(frames)
    ]
    output = BytesIO()
    try:
        images[0].save(
            output,
            "GIF",
            save_all=True,
            append_images=images[1:],
            duration=100,
            loop=0,
            optimize=False,
        )
        return output.getvalue()
    finally:
        for image in images:
            image.close()


class TestAvatarFrames(unittest.TestCase):
    def test_static_uses_identical_first_frame_and_mask(self) -> None:
        data = gif()
        animated = load_avatar(data, Box(2, 3, 16, 16))
        static = load_avatar(data, Box(2, 3, 16, 16), mode="static")
        if animated is None or static is None:
            self.fail("GIF did not decode")
        self.assertEqual(len(animated.frames), 2)
        self.assertEqual(len(static.frames), 1)
        self.assertEqual(static.frames[0].tobytes(), animated.frames[0].tobytes())
        self.assertEqual(static.at(0).tobytes(), static.at(0.5).tobytes())
        self.assertEqual(static.frames[0].getpixel((0, 0)), (0, 0, 255, 0))
        self.assertEqual((static.x, static.y), (2, 3))

    def test_damaged_later_pixels_do_not_reject_valid_static_frame(self) -> None:
        damaged = gif()[:-8]
        self.assertIsNotNone(load_avatar(damaged, Box(0, 0, 16, 16), mode="static"))
        with self.assertRaises(ValueError):
            load_avatar(damaged, Box(0, 0, 16, 16), mode="animated")

    def test_animation_frame_budget_does_not_decode_unused_static_frames(self) -> None:
        data = gif(201)
        static = load_avatar(data, Box(0, 0, 16, 16), mode="static")
        if static is None:
            self.fail("Missing first frame")
        self.assertEqual(len(static.frames), 1)
        with self.assertRaisesRegex(ValueError, "200 frames"):
            load_avatar(data, Box(0, 0, 16, 16))

    def test_static_rejects_invalid_first_frame_and_compressed_or_pixel_overflow(
        self,
    ) -> None:
        data = gif()
        too_large = data[:6] + pack("<HH", 8192, 8192) + data[10:]
        for value in (b"broken", data[:800], b"x" * (4 * 1024 * 1024 + 1), too_large):
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError):
                    load_avatar(value, Box(0, 0, 16, 16), mode="static")
        with self.assertRaisesRegex(ValueError, "target exceeds"):
            load_avatar(data, Box(0, 0, 2048, 2048), mode="static")

    def test_single_frame_and_non_gif_keep_existing_static_path(self) -> None:
        for mode in ("static", "animated"):
            self.assertIsNone(load_avatar(gif(1), Box(0, 0, 16, 16), mode=mode))
            for fmt in ("PNG", "WEBP"):
                output = BytesIO()
                with Image.new("RGBA", (16, 16), "blue") as source:
                    source.save(output, fmt)
                self.assertIsNone(
                    load_avatar(output.getvalue(), Box(0, 0, 16, 16), mode=mode)
                )
