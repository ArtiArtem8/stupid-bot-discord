from io import BytesIO

from PIL import Image


class ImageProcessingError(Exception):
    """Raised when a Wolfram plot cannot be processed."""


class ImageOutputTooLargeError(ImageProcessingError):
    """Raised when the processed plot cannot fit the upload budget."""


def _calculate_output_size(
    source_size: tuple[int, int],
    *,
    max_size: tuple[int, int],
) -> tuple[int, int]:
    """Return the source size or an aspect-preserving downscaled size."""
    source_width, source_height = source_size
    max_width, max_height = max_size
    if min(source_width, source_height, max_width, max_height) <= 0:
        raise ImageProcessingError("Image dimensions must be positive")

    scale = min(
        1.0,
        max_width / source_width,
        max_height / source_height,
    )
    return (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )


def _encode_webp(
    image: Image.Image,
    *,
    lossless: bool,
    quality: int,
) -> bytes:
    """Encode an image to WebP entirely in memory."""
    with BytesIO() as output:
        image.save(
            output,
            format="WEBP",
            lossless=lossless,
            quality=quality,
            method=6,
        )
        return output.getvalue()


def _encode_with_budget(
    image: Image.Image,
    *,
    max_output_bytes: int,
) -> bytes:
    """Try lossless WebP, then one high-quality lossy fallback."""
    if max_output_bytes <= 0:
        raise ImageOutputTooLargeError("Image upload budget must be positive")

    lossless = _encode_webp(image, lossless=True, quality=100)
    if len(lossless) <= max_output_bytes:
        return lossless

    lossy = _encode_webp(image, lossless=False, quality=95)
    if len(lossy) <= max_output_bytes:
        return lossy
    raise ImageOutputTooLargeError("Processed plot exceeds the upload budget")


def process_wolfram_plot(
    source: bytes,
    *,
    max_size: tuple[int, int],
    max_source_pixels: int,
    max_output_bytes: int,
) -> bytes:
    """Downscale only when needed, then encode the complete plot as WebP."""
    if not source:
        raise ImageProcessingError("Wolfram plot response is empty")
    if max_source_pixels <= 0:
        raise ImageProcessingError("Source pixel limit must be positive")

    try:
        with BytesIO(source) as input_buffer, Image.open(input_buffer) as opened:
            source_pixels = opened.width * opened.height
            if source_pixels > max_source_pixels:
                raise ImageProcessingError(
                    "Wolfram plot exceeds the source pixel limit"
                )

            output_size = _calculate_output_size(
                opened.size,
                max_size=max_size,
            )
            with opened.convert("RGB") as rgb:
                if output_size == rgb.size:
                    return _encode_with_budget(
                        rgb,
                        max_output_bytes=max_output_bytes,
                    )
                with rgb.resize(output_size, Image.Resampling.LANCZOS) as resized:
                    return _encode_with_budget(
                        resized,
                        max_output_bytes=max_output_bytes,
                    )
    except ImageProcessingError:
        raise
    except (Image.DecompressionBombError, OSError, ValueError) as error:
        raise ImageProcessingError("Failed to process Wolfram plot") from error
