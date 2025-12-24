import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, Mock, patch

import requests
from PIL import Image, features
from requests.exceptions import HTTPError, Timeout

from utils import (
    convert_image,
    optimize_image,
    save_image,
)
from utils.image_utils import generate_unique_filename


class TestImageUtils(unittest.TestCase):
    def setUp(self) -> None:
        """Create a temporary directory for test outputs."""
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        """Remove the temporary directory after tests."""
        shutil.rmtree(self.test_dir)

    def _create_dummy_image_bytes(
        self,
        format: str = "PNG",
        size: tuple[int, int] = (100, 100),
        color: str = "red",
    ) -> bytes:
        """Helper to create image bytes in memory without saving to disk."""
        with BytesIO() as bio:
            img = Image.new("RGB", size, color)
            img.save(bio, format=format)
            return bio.getvalue()

    def _create_dummy_file(
        self, name: str, format: str = "PNG", size: tuple[int, int] = (100, 100)
    ) -> Path:
        """Helper to create a physical image file in the test temp dir."""
        path = self.test_dir / name
        img = Image.new("RGB", size, "blue")
        img.save(path, format=format)
        return path

    def _create_dummy_rgba_image_bytes(
        self, format: str = "PNG", size: tuple[int, int] = (64, 32)
    ) -> bytes:
        with BytesIO() as bio:
            img = Image.new("RGBA", size, (255, 0, 0, 128))
            img.save(bio, format=format)
            return bio.getvalue()

    def _create_dummy_rgba_file(self, name: str, format: str = "PNG") -> Path:
        path = self.test_dir / name
        img = Image.new("RGBA", (64, 32), (10, 20, 30, 128))
        img.save(path, format=format)
        return path

    @patch("utils.image_utils.requests.get")
    def test_save_image_success(self, mock_get: MagicMock) -> None:
        """Test downloading and saving an image successfully."""
        image_data = self._create_dummy_image_bytes(format="PNG")
        mock_response = MagicMock()
        mock_response.content = image_data
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        saved_path = save_image(
            image_url="http://example.com/image.png",
            save_to=self.test_dir,
            format="WEBP",
        )

        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.suffix, ".webp")
        self.assertEqual(saved_path.parent, self.test_dir)

        with Image.open(saved_path) as img:
            self.assertEqual(img.format, "WEBP")

    @patch("utils.image_utils.requests.get")
    def test_save_image_with_resize(self, mock_get: MagicMock) -> None:
        """Test that image is resized correctly during save."""
        image_data = self._create_dummy_image_bytes(size=(200, 200))
        mock_response = MagicMock()
        mock_response.content = image_data
        mock_get.return_value = mock_response

        saved_path = save_image(
            image_url="http://example.com/huge.jpg",
            save_to=self.test_dir,
            resize=(100, None),
            format="JPEG",
        )

        with Image.open(saved_path) as img:
            self.assertEqual(img.size, (100, 100))
            self.assertEqual(img.format, "JPEG")

    @patch("utils.image_utils.requests.get")
    def test_save_image_network_error(self, mock_get: MagicMock) -> None:
        """Test handling of network errors."""
        mock_get.side_effect = Timeout("Connection timed out")

        with self.assertRaises(RuntimeError) as context:
            save_image("http://broken.com/img", self.test_dir)

        self.assertIn("Image processing failed", str(context.exception))

    @patch("utils.image_utils.requests.get")
    def test_save_image_http_error(self, mock_get: MagicMock) -> None:
        """Test handling of 404/500 errors."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = HTTPError("404 Not Found")
        mock_get.return_value = mock_resp

        with self.assertRaises(RuntimeError):
            save_image("http://example.com/404", self.test_dir)

    def test_generate_unique_filename(self) -> None:
        """Test filename generation format."""
        path = generate_unique_filename("jpg")
        self.assertIsInstance(path, Path)
        self.assertEqual(path.suffix, ".jpg")
        self.assertEqual(len(path.stem), 16)

    def test_optimize_image_inplace(self) -> None:
        """Test optimizing an existing image in place."""
        input_path = self._create_dummy_file("test_opt.png", size=(500, 500))

        output_path = optimize_image(
            input_path=input_path, max_size=(250, 250), quality=80
        )

        self.assertEqual(input_path, output_path)
        with Image.open(output_path) as img:
            self.assertEqual(img.size, (250, 250))

    def test_optimize_image_new_path(self) -> None:
        """Test optimizing to a new location."""
        input_path = self._create_dummy_file("source.jpg", format="JPEG")
        output_path = self.test_dir / "dest.jpg"

        result = optimize_image(input_path, output_path=output_path)

        self.assertTrue(output_path.exists())
        self.assertTrue(input_path.exists())
        self.assertEqual(result, output_path)

    def test_optimize_invalid_file(self) -> None:
        """Test optimization with a non-image file."""
        text_file = self.test_dir / "not_image.txt"
        text_file.write_text("I am not an image")

        with self.assertRaises(ValueError):
            optimize_image(text_file)

    def test_convert_image_format(self) -> None:
        """Test converting PNG to JPEG."""
        input_path = self._create_dummy_file("test.png", format="PNG")

        output_path = convert_image(
            input_path=input_path, output_format="JPEG", quality=90
        )

        self.assertEqual(output_path.suffix, ".jpeg")
        with Image.open(output_path) as img:
            self.assertEqual(img.format, "JPEG")

    def test_convert_invalid_format_arg(self) -> None:
        """Test catching invalid format strings."""
        input_path = self._create_dummy_file("test.png")

        with self.assertRaises(ValueError):
            convert_image(input_path, output_format="BMP")

    def test_convert_missing_file(self) -> None:
        """Test converting a non-existent file."""
        with self.assertRaises(RuntimeError):
            convert_image(self.test_dir / "ghost.png", "JPEG")

    @patch("utils.image_utils.requests.get")
    def test_save_image_png_output(self, mock_get: MagicMock) -> None:
        """Covers PNG branch (compress_level path) + output suffix correctness."""
        image_data = self._create_dummy_image_bytes(format="PNG", size=(40, 20))

        resp = Mock(spec=requests.Response)
        resp.content = image_data
        resp.raise_for_status = Mock()
        mock_get.return_value = cast(requests.Response, resp)

        saved_path = save_image(
            image_url="http://example.com/image.png",
            save_to=self.test_dir,
            format="PNG",
            quality=90,
        )

        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.suffix, ".png")
        with Image.open(saved_path) as img:
            self.assertEqual(img.format, "PNG")
            self.assertEqual(img.size, (40, 20))

    @patch("utils.image_utils.requests.get")
    def test_save_image_bad_payload_raises(self, mock_get: MagicMock) -> None:
        """If HTTP returns non-image bytes, save_image should raise RuntimeError."""
        resp = Mock(spec=requests.Response)
        resp.content = b"this is not an image"
        resp.raise_for_status = Mock()
        mock_get.return_value = cast(requests.Response, resp)

        with self.assertRaises(RuntimeError) as ctx:
            save_image(
                image_url="http://example.com/not-image",
                save_to=self.test_dir,
                format="WEBP",
            )

        self.assertIn("Image processing failed", str(ctx.exception))

    @patch("utils.image_utils.requests.get")
    def test_save_image_rgba_converts_for_jpeg(self, mock_get: MagicMock) -> None:
        """Covers RGBA->RGB conversion path for JPEG/WEBP outputs."""
        rgba_png = self._create_dummy_rgba_image_bytes(format="PNG", size=(60, 30))

        resp = Mock(spec=requests.Response)
        resp.content = rgba_png
        resp.raise_for_status = Mock()
        mock_get.return_value = cast(requests.Response, resp)

        saved_path = save_image(
            image_url="http://example.com/rgba.png",
            save_to=self.test_dir,
            format="JPEG",
        )

        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.suffix, ".jpeg")
        with Image.open(saved_path) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.mode, "RGB")
            self.assertEqual(img.size, (60, 30))

    def test_convert_image_rgba_to_jpeg(self) -> None:
        """RGBA PNG -> JPEG should succeed due to RGB conversion in convert_image."""
        input_path = self._create_dummy_rgba_file("rgba.png", format="PNG")

        out = convert_image(input_path=input_path, output_format="JPEG", quality=85)

        self.assertTrue(out.exists())
        self.assertEqual(out.suffix, ".jpeg")
        with Image.open(out) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.mode, "RGB")

    def test_convert_image_invalid_source_is_value_error(self) -> None:
        """Non-image source should raise ValueError (UnidentifiedImageError mapped)."""
        bad = self.test_dir / "not_an_image.bin"
        bad.write_bytes(b"nope")

        with self.assertRaises(ValueError):
            convert_image(bad, output_format="PNG")

    def test_optimize_image_png_branch_smoke(self) -> None:
        """Covers optimize_image PNG compress_level branch.
        Does not assert file size.
        """
        input_path = self._create_dummy_file("opt.png", format="PNG", size=(90, 45))
        out = optimize_image(input_path=input_path, quality=80)

        self.assertEqual(out, input_path)
        with Image.open(out) as img:
            self.assertEqual(img.format, "PNG")
            self.assertEqual(img.size, (90, 45))

    def test_optimize_image_webp_branch_if_supported(self) -> None:
        """Covers optimize_image WEBP method branch if the codec is available."""
        if not features.check_module("webp"):
            self.skipTest("WebP not supported in this Pillow build")

        src = self.test_dir / "src.webp"
        Image.new("RGB", (120, 60), "green").save(src, format="WEBP")

        out = optimize_image(input_path=src, quality=80, max_size=(50, 50))
        self.assertEqual(out, src)

        with Image.open(out) as img:
            self.assertEqual(img.format, "WEBP")
            self.assertEqual(img.size, (50, 25))


if __name__ == "__main__":
    unittest.main()
