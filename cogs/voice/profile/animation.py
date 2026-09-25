"""Restrained feature-flag motion with one shared GIF palette."""

import logging
from io import BytesIO
from math import cos, pi, sin

from PIL import Image, ImageColor, ImageDraw

from api.progression.appearance import AppearanceFeature
from api.voice.profile.model import VoiceProfile
from cogs.voice.profile import theme
from cogs.voice.profile.renderer import CardIdentity, draw_card, render_png

logger = logging.getLogger(__name__)
FRAME_COUNT = 24
FRAME_MS = 120
GIF_HARD_LIMIT = 5 * 1024 * 1024


def render_gif(
    profile: VoiceProfile, identity: CardIdentity, *, palette_colors: int = 128
) -> bytes:
    """Render one loop; geometry stays fixed and quantization shares a palette."""
    base = draw_card(profile, identity)
    palette_source = base.resize(theme.SIZE, Image.Resampling.LANCZOS)
    palette_draw = ImageDraw.Draw(palette_source)
    for index in range(FRAME_COUNT):
        palette_draw.rectangle(
            (index * 8, 650, index * 8 + 7, 674),
            fill=_prismatic_color(index / FRAME_COUNT),
        )
    palette = palette_source.quantize(
        colors=palette_colors, method=Image.Quantize.MEDIANCUT
    )
    frames: list[Image.Image] = []
    try:
        for index in range(FRAME_COUNT):
            frame = base.copy()
            _draw_motion(ImageDraw.Draw(frame, "RGBA"), profile, index)
            small = frame.resize(theme.SIZE, Image.Resampling.LANCZOS)
            frames.append(small.quantize(palette=palette, dither=Image.Dither.NONE))
            frame.close()
            small.close()
        buffer = BytesIO()
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_MS,
            loop=0,
            disposal=2,
            optimize=True,
        )
        return buffer.getvalue()
    finally:
        base.close()
        palette_source.close()
        palette.close()
        for frame in frames:
            frame.close()


def _draw_motion(draw: ImageDraw.ImageDraw, profile: VoiceProfile, index: int) -> None:
    """Apply feature-flag motion to only the narrow accent regions."""
    phase = index / FRAME_COUNT
    features = profile.appearance.features
    accent = profile.appearance.color
    rgb = ((accent >> 16) & 255, (accent >> 8) & 255, accent & 255)
    if features & AppearanceFeature.BORDER_MOTION:
        x = round((70 + 1020 * (0.5 - 0.5 * cos(2 * pi * phase))) * 2)
        draw.line((x - 32, 56, x + 32, 56), fill=(*rgb, 95), width=3)
    if features & AppearanceFeature.PROGRESS_SHEEN:
        fill_end = 58 + round(1084 * profile.progress_ratio)
        if fill_end > 70:
            x = round((58 + (fill_end - 58) * phase) * 2)
            opacity = round(78 * sin(pi * phase))
            draw.line((x, 493, x + 10, 515), fill=(255, 255, 255, opacity), width=3)
    if features & AppearanceFeature.SECONDARY_ACCENT:
        draw.line((2240, 76, 2296, 76), fill=(*rgb, 115), width=3)
    if features & AppearanceFeature.PRISMATIC_ACCENT:
        color = _prismatic_color(phase)
        x = round((960 + 110 * (0.5 - 0.5 * cos(2 * pi * phase))) * 2)
        draw.line((x, 76, x + 26, 76), fill=color, width=3)


def _prismatic_color(phase: float) -> tuple[int, int, int]:
    """Interpolate a closed, small-area color cycle across the loop seam."""
    position = phase * len(theme.PRISM)
    index = int(position)
    fraction = position - index
    first = ImageColor.getrgb(theme.PRISM[index % len(theme.PRISM)])
    second = ImageColor.getrgb(theme.PRISM[(index + 1) % len(theme.PRISM)])
    return (
        round(first[0] + (second[0] - first[0]) * fraction),
        round(first[1] + (second[1] - first[1]) * fraction),
        round(first[2] + (second[2] - first[2]) * fraction),
    )


def render_attachment(
    profile: VoiceProfile, identity: CardIdentity
) -> tuple[bytes, str]:
    """Select motion from appearance; any GIF failure falls back to PNG."""
    if profile.appearance.features & AppearanceFeature.BORDER_MOTION:
        try:
            gif = render_gif(profile, identity)
            if len(gif) <= GIF_HARD_LIMIT:
                return gif, "gif"
            logger.warning("Voice profile GIF exceeded size budget: %d bytes", len(gif))
            smaller = render_gif(profile, identity, palette_colors=96)
            if len(smaller) <= GIF_HARD_LIMIT:
                return smaller, "gif"
            logger.warning(
                "Voice profile optimized GIF still oversized: %d bytes", len(smaller)
            )
        except Exception as exc:
            logger.warning(
                "Voice profile GIF failed; using PNG: %s", type(exc).__name__
            )
            logger.debug("GIF fallback traceback", exc_info=True)
    return render_png(profile, identity), "png"
