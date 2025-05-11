import random
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, UnidentifiedImageError


def save_image(
    image_url: str,
    save_to: Path,
    resize: tuple[int, int | None] | None = None,
    quality: int = 95,
    format: str = "WEBP",
) -> Path:
    """Download and process an image from URL with various optimizations.

    Args:
        image_url: URL of the source image
        save_to: Directory to save the processed image
        resize: Optional (width, height) tuple for resizing
        quality: Image quality (1-100)
        format: Output format (WEBP/JPEG/PNG)

    Returns:
        Path to saved image file

    """
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()

        with Image.open(BytesIO(response.content)) as img:
            # Convert to RGB for JPEG/WEBP formats
            if format in ("JPEG", "WEBP") and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            if resize:
                resize = (resize[0], img.size[1] * resize[0] // img.size[0])
                img = img.resize(resize, Image.Resampling.LANCZOS)

            filename = generate_unique_filename(format.lower())
            output_path = save_to / filename

            save_args: dict[str, Any] = {
                "format": format,
                "quality": quality,
                "optimize": True,
            }

            if format == "WEBP":
                save_args["method"] = 6  # Highest compression
            elif format == "PNG":
                save_args["compress_level"] = 9  # Max compression

            img.save(output_path, **save_args)
            return output_path

    except Exception as e:
        raise RuntimeError(f"Image processing failed: {e!s}") from e


def generate_unique_filename(extension: str) -> Path:
    """Generate unique random filename with given extension."""
    return Path(f"{random.randint(2**27, 2**28)}").with_suffix(f".{extension}")


def optimize_image(
    input_path: Path,
    output_path: Optional[Path] = None,
    quality: int = 85,
    max_size: tuple[int, int] | None = None,
) -> Path:
    """Optimize existing image file for web use.

    Args:
        input_path: Path to source image
        output_path: Optional output path (uses input path if None)
        quality: Image quality (1-100)
        max_size: Optional maximum dimensions (width, height)

    Returns:
        Path to optimized image

    """
    try:
        output_path = output_path or input_path

        with Image.open(input_path) as img:
            if max_size:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

            save_args: dict[str, Any] = {
                "quality": quality,
                "optimize": True,
            }

            if img.format == "PNG":
                save_args["compress_level"] = 9
            elif img.format == "WEBP":
                save_args["method"] = 6

            img.save(output_path, **save_args)
            return output_path

    except UnidentifiedImageError as e:
        raise ValueError("Unsupported image format") from e
    except Exception as e:
        raise RuntimeError(f"Image optimization failed: {e!s}") from e


def convert_image(
    input_path: Path,
    output_format: str,
    output_path: Path | None = None,
    quality: int = 85,
) -> Path:
    """Convert image between formats with optional quality setting.

    Args:
        input_path: Path to source image
        output_format: Target format (WEBP/JPEG/PNG)
        output_path: Optional output path
        quality: Image quality (1-100)

    Returns:
        Path to converted image

    """
    valid_formats = ("WEBP", "JPEG", "PNG")
    if output_format.upper() not in valid_formats:
        raise ValueError(f"Invalid format. Must be one of {valid_formats}")

    output_path = output_path or input_path.with_suffix(f".{output_format.lower()}")

    try:
        with Image.open(input_path) as img:
            if output_format in ("JPEG", "WEBP") and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            save_args: dict[str, Any] = {
                "format": output_format,
                "quality": quality,
                "optimize": True,
            }

            img.save(output_path, **save_args)
            return output_path

    except UnidentifiedImageError as e:
        raise ValueError("Unsupported source image format") from e
    except Exception as e:
        raise RuntimeError(f"Image conversion failed: {e!s}") from e
