"""Tests for the full-image in-memory Wolfram plot processor."""

import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

import utils.image_utils as image_utils
from utils.image_utils import (
    ImageOutputTooLargeError,
    ImageProcessingError,
    _calculate_output_size,
    _encode_webp,
    _encode_with_budget,
    process_wolfram_plot,
)


class TestWolframPlotProcessing(unittest.TestCase):
    def _process(
        self,
        source: bytes,
        *,
        target_width: int = 800,
        max_size: tuple[int, int] = (1200, 1200),
        max_source_pixels: int = 25_000_000,
        max_output_bytes: int = 9 * 1024 * 1024,
        quality: int = 90,
        fallback_qualities: tuple[int, ...] = (82,),
    ) -> bytes:
        return process_wolfram_plot(
            source,
            target_width=target_width,
            max_size=max_size,
            max_source_pixels=max_source_pixels,
            max_output_bytes=max_output_bytes,
            quality=quality,
            fallback_qualities=fallback_qualities,
        )

    def _synthetic_source(
        self,
        image_format: str = "PNG",
        *,
        mode: str = "RGB",
        size: tuple[int, int] = (437, 214),
    ) -> bytes:
        with BytesIO() as buffer:
            if mode == "P":
                with Image.new("P", size, 0) as image:
                    image.putpalette([255, 255, 255, 0, 0, 0] + [0] * 762)
                    image.putpixel((min(20, size[0] - 1), size[1] // 2), 1)
                    image.save(buffer, format=image_format)
            else:
                color = (255, 255, 255, 0) if mode == "RGBA" else "white"
                with Image.new(mode, size, color) as image:
                    marker = (20, 20, 20, 255) if mode == "RGBA" else (20, 20, 20)
                    image.putpixel((min(20, size[0] - 1), size[1] // 2), marker)
                    image.save(buffer, format=image_format)
            return buffer.getvalue()

    def test_png_gif_and_jpeg_sources_become_webp(self) -> None:
        for image_format in ("PNG", "GIF", "JPEG"):
            with self.subTest(image_format=image_format):
                output = self._process(self._synthetic_source(image_format))
                with Image.open(BytesIO(output)) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.size, (800, 392))

    def test_processing_uses_full_source_dimensions_without_crop(self) -> None:
        source = self._synthetic_source(size=(437, 214))
        with patch(
            "utils.image_utils._calculate_output_size",
            wraps=_calculate_output_size,
        ) as calculate:
            self._process(source)

        calculate.assert_called_once_with(
            (437, 214), target_width=800, max_size=(1200, 1200)
        )

    def test_output_fits_budget(self) -> None:
        output = self._process(self._synthetic_source(), max_output_bytes=20_000)
        self.assertLessEqual(len(output), 20_000)

    def test_primary_quality_stops_fallback_when_it_fits(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch("utils.image_utils._encode_webp", return_value=b"fit") as encode:
                output = _encode_with_budget(
                    image, qualities=(90, 82), max_output_bytes=3
                )

        self.assertEqual(output, b"fit")
        encode.assert_called_once_with(image, quality=90)

    def test_quality_fallback_is_used_after_oversized_primary(self) -> None:
        encoded = {90: b"large", 82: b"fits"}

        def fake_encode(_image: Image.Image, *, quality: int) -> bytes:
            return encoded[quality]

        with Image.new("RGB", (10, 10)) as image:
            with patch.object(
                image_utils,
                "_encode_webp",
                side_effect=fake_encode,
            ) as encode:
                output = _encode_with_budget(
                    image, qualities=(90, 82), max_output_bytes=4
                )

        self.assertEqual(output, b"fits")
        self.assertEqual(
            [call.kwargs["quality"] for call in encode.call_args_list], [90, 82]
        )

    def test_all_qualities_oversized_raises(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch("utils.image_utils._encode_webp", return_value=b"too large"):
                with self.assertRaises(ImageOutputTooLargeError):
                    _encode_with_budget(image, qualities=(90, 82), max_output_bytes=4)

    def test_encoding_never_uses_more_than_one_fallback(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch(
                "utils.image_utils._encode_webp", return_value=b"too large"
            ) as encode:
                with self.assertRaises(ImageOutputTooLargeError):
                    _encode_with_budget(
                        image, qualities=(90, 82, 74, 66), max_output_bytes=4
                    )

        self.assertEqual(
            [call.kwargs["quality"] for call in encode.call_args_list], [90, 82]
        )

    def test_invalid_and_empty_sources_raise_processing_error(self) -> None:
        for source in (b"not an image", b""):
            with self.subTest(source=source):
                with self.assertRaises(ImageProcessingError):
                    self._process(source)

    def test_source_pixel_limit_is_enforced(self) -> None:
        with self.assertRaises(ImageProcessingError):
            self._process(self._synthetic_source(), max_source_pixels=437 * 214 - 1)

    def test_rgba_and_palette_sources_are_converted_to_rgb_webp(self) -> None:
        sources = (
            self._synthetic_source("PNG", mode="RGBA"),
            self._synthetic_source("GIF", mode="P"),
        )
        for source in sources:
            with self.subTest():
                output = self._process(source)
                with Image.open(BytesIO(output)) as image:
                    self.assertEqual(image.mode, "RGB")

    def test_narrow_images_are_processed_without_special_cases(self) -> None:
        for width, expected_width in ((80, 600), (102, 765)):
            with self.subTest(width=width):
                output = self._process(self._synthetic_source(size=(width, 160)))
                with Image.open(BytesIO(output)) as image:
                    self.assertEqual(image.size, (expected_width, 1200))

    def test_dimensions_preserve_aspect_ratio_and_max_size(self) -> None:
        self.assertEqual(
            _calculate_output_size((437, 214), target_width=800, max_size=(600, 300)),
            (600, 294),
        )

    def test_encode_webp_uses_decodable_webp(self) -> None:
        with Image.new("RGB", (20, 10), "blue") as image:
            output = _encode_webp(image, quality=90)
        with Image.open(BytesIO(output)) as decoded:
            self.assertEqual(decoded.format, "WEBP")


if __name__ == "__main__":
    unittest.main()
