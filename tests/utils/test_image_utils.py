"""Tests for the full-image in-memory Wolfram plot processor."""

import unittest
from io import BytesIO
from unittest.mock import call, patch

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
        max_size: tuple[int, int] = (1200, 1200),
        max_source_pixels: int = 25_000_000,
        max_output_bytes: int = 9 * 1024 * 1024,
    ) -> bytes:
        return process_wolfram_plot(
            source,
            max_size=max_size,
            max_source_pixels=max_source_pixels,
            max_output_bytes=max_output_bytes,
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

    def test_png_gif_and_jpeg_sources_become_webp_without_upscale(self) -> None:
        for image_format in ("PNG", "GIF", "JPEG"):
            with self.subTest(image_format=image_format):
                output = self._process(self._synthetic_source(image_format))
                with Image.open(BytesIO(output)) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.size, (437, 214))

    def test_source_inside_max_size_preserves_dimensions(self) -> None:
        source = self._synthetic_source(size=(1000, 1100))

        output = self._process(source)

        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.size, (1000, 1100))

    def test_large_source_is_downscaled_proportionally(self) -> None:
        source = self._synthetic_source(size=(2400, 1800))

        output = self._process(source)

        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.size, (1200, 900))

    def test_resize_is_not_called_when_source_fits(self) -> None:
        source = self._synthetic_source(size=(800, 600))

        with patch.object(Image.Image, "resize", autospec=True) as resize:
            self._process(source)

        resize.assert_not_called()

    def test_processing_uses_full_source_dimensions_without_crop(self) -> None:
        source = self._synthetic_source(size=(437, 214))
        with patch.object(
            image_utils,
            "_calculate_output_size",
            wraps=_calculate_output_size,
        ) as calculate:
            self._process(source)

        calculate.assert_called_once_with((437, 214), max_size=(1200, 1200))

    def test_output_fits_budget(self) -> None:
        output = self._process(self._synthetic_source(), max_output_bytes=20_000)
        self.assertLessEqual(len(output), 20_000)

    def test_lossless_primary_stops_fallback_when_it_fits(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch.object(
                image_utils,
                "_encode_webp",
                return_value=b"fit",
            ) as encode:
                output = _encode_with_budget(image, max_output_bytes=3)

        self.assertEqual(output, b"fit")
        encode.assert_called_once_with(image, lossless=True, quality=100)

    def test_lossy_fallback_runs_only_after_oversized_lossless(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch.object(
                image_utils,
                "_encode_webp",
                side_effect=(b"large", b"fit"),
            ) as encode:
                output = _encode_with_budget(image, max_output_bytes=4)

        self.assertEqual(output, b"fit")
        self.assertEqual(
            encode.call_args_list,
            [
                call(image, lossless=True, quality=100),
                call(image, lossless=False, quality=95),
            ],
        )

    def test_encoding_has_at_most_two_attempts(self) -> None:
        with Image.new("RGB", (10, 10)) as image:
            with patch.object(
                image_utils,
                "_encode_webp",
                return_value=b"too large",
            ) as encode:
                with self.assertRaises(ImageOutputTooLargeError):
                    _encode_with_budget(image, max_output_bytes=4)

        self.assertEqual(
            encode.call_args_list,
            [
                call(image, lossless=True, quality=100),
                call(image, lossless=False, quality=95),
            ],
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

    def test_output_size_never_upscales(self) -> None:
        self.assertEqual(
            _calculate_output_size((437, 214), max_size=(1200, 1200)),
            (437, 214),
        )

    def test_output_size_preserves_aspect_ratio_with_both_bounds(self) -> None:
        self.assertEqual(
            _calculate_output_size((1600, 1000), max_size=(1200, 600)),
            (960, 600),
        )

    def test_encode_webp_uses_decodable_lossless_webp(self) -> None:
        with Image.new("RGB", (20, 10), "blue") as image:
            output = _encode_webp(image, lossless=True, quality=100)
        with Image.open(BytesIO(output)) as decoded:
            self.assertEqual(decoded.format, "WEBP")
            self.assertEqual(decoded.getpixel((0, 0)), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
