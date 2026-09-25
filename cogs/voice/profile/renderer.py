"""Pillow rendering of one fixed voice profile card at supersampled size."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import ceil

from PIL import Image, ImageDraw, ImageFont, ImageOps

from api.voice.profile.model import DailyActivityPoint, VoiceProfile
from cogs.voice.profile import theme


@dataclass(frozen=True, slots=True)
class CardIdentity:
    """Resolved Discord display values; avatar bytes may be unavailable."""

    username: str
    display_name: str
    guild_name: str
    companion_name: str
    avatar_png: bytes | None = None


def _font(size: int, weight: int = 400, scale: int = 2) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(theme.FONT_PATH, size * scale)
    font.set_variation_by_axes([14, weight])
    return font


def _color(value: int) -> tuple[int, int, int]:
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _fit(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> str:
    """Keep identity and insight values on one line, including unsupported glyphs."""
    safe = text.encode("utf-8", "replace").decode("utf-8")
    if draw.textlength(safe, font=font) <= width:
        return safe
    while safe and draw.textlength(safe + "…", font=font) > width:
        safe = safe[:-1]
    return safe + "…"


def _duration(seconds: float) -> str:
    minutes = int(seconds) // 60
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:,}h {minute:02d}m"
    return f"{minute}m"


def _display_name(identity: CardIdentity, scale: int) -> str:
    try:
        font = _font(32, 700, scale)
        missing = font.getmask("\ufffd")
        missing_box = font.getbbox("\ufffd")
        missing_pixels = round(
            (missing_box[2] - missing_box[0]) * (missing_box[3] - missing_box[1])
        )
        for character in identity.display_name:
            if character.isspace() or font.getbbox(character) != missing_box:
                continue
            mask = font.getmask(character)
            if all(mask[index] == missing[index] for index in range(missing_pixels)):
                return identity.username
    except (OSError, UnicodeError):
        return identity.username
    return identity.display_name


def _insights(
    profile: VoiceProfile, identity: CardIdentity
) -> tuple[tuple[str, str], ...]:
    peak = (
        f"{profile.stats.peak_hour:02d}:00"
        if profile.stats.peak_hour is not None
        else "—"
    )
    return (
        ("TOP COMPANION", identity.companion_name),
        ("PEAK HOUR", peak),
        ("XP LAST 7 DAYS", f"+{profile.stats.xp_last_7_days:,} XP"),
    )


def _avatar(
    image: Image.Image, identity: CardIdentity, accent: tuple[int, int, int], scale: int
) -> None:
    side = 104 * scale
    xy = (63 * scale, 61 * scale)
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (
            xy[0] - 3 * scale,
            xy[1] - 3 * scale,
            xy[0] + side + 3 * scale,
            xy[1] + side + 3 * scale,
        ),
        fill=accent,
    )
    try:
        if identity.avatar_png is None:
            raise ValueError("No avatar")
        with Image.open(BytesIO(identity.avatar_png)) as source:
            avatar = ImageOps.fit(
                source.convert("RGB"), (side, side), method=Image.Resampling.LANCZOS
            )
    except (OSError, ValueError):
        avatar = Image.new("RGB", (side, side), theme.TRACK)
        initials = (
            "".join(part[:1] for part in identity.display_name.split()[:2]).upper()
            or "?"
        )
        placeholder = ImageDraw.Draw(avatar)
        font = _font(33, 700, scale)
        box = placeholder.textbbox((0, 0), initials, font=font)
        placeholder.text(
            ((side - (box[2] - box[0])) / 2, (side - (box[3] - box[1])) / 2 - box[1]),
            initials,
            font=font,
            fill=theme.TEXT,
        )
    mask = Image.new("L", (side, side))
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    image.paste(avatar, xy, mask)


def draw_card(
    profile: VoiceProfile, identity: CardIdentity, *, scale: int = 2
) -> Image.Image:
    """Draw fixed geometry and data; animation overlays use this same frame."""
    s = scale
    image = Image.new("RGB", (1200 * s, 675 * s), theme.BACKGROUND)
    d = ImageDraw.Draw(image)
    accent = _color(profile.appearance.color)

    def box(
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        radius: int,
        fill: str | tuple[int, int, int],
        outline: str | None = None,
    ) -> None:
        d.rounded_rectangle(
            (x0 * s, y0 * s, x1 * s, y1 * s),
            radius * s,
            fill=fill,
            outline=outline,
            width=s,
        )

    def label(
        x: int,
        y: int,
        value: str,
        size: int,
        weight: int = 400,
        color: str | tuple[int, int, int] = theme.TEXT,
        max_width: int | None = None,
    ) -> None:
        font = _font(size, weight, s)
        if max_width is not None:
            value = _fit(d, value, font, max_width * s)
        d.text((x * s, y * s), value, font=font, fill=color, stroke_width=0)

    box(25, 25, 1175, 650, 25, theme.SURFACE, theme.BORDER)
    d.rectangle((51 * s, 51 * s, 54 * s, 190 * s), fill=accent)
    _avatar(image, identity, accent, s)
    label(190, 66, _display_name(identity, s), 32, 700, max_width=625)
    label(191, 111, identity.guild_name, 16, color=theme.MUTED, max_width=620)
    label(191, 145, "VOICE PROFILE  /  SERVER LIFETIME", 11, 600, theme.QUIET)
    label(911, 56, profile.appearance.tier.value.upper(), 15, 700, accent, 235)
    label(908, 78, f"LVL {profile.level:,}", 49, 700, max_width=245)
    label(910, 145, f"{profile.total_xp:,} XP", 24, 600, theme.TEXT, 240)
    d.line((56 * s, 204 * s, 1144 * s, 204 * s), fill=theme.BORDER, width=s)
    label(58, 217, "LEVEL PROGRESS", 11, 600, theme.MUTED)
    label(
        891,
        215,
        f"{profile.level_earned_xp:,} / {profile.level_required_xp:,} XP",
        12,
        600,
        theme.MUTED,
        250,
    )
    box(58, 245, 1142, 259, 7, theme.TRACK)
    fill_end = 58 + round(1084 * profile.progress_ratio)
    if fill_end > 58:
        box(58, 245, fill_end, 259, 7, accent)
    for mark in range(1, 10):
        x = (58 + mark * 1084 // 10) * s
        d.line((x, 248 * s, x, 256 * s), fill=theme.SURFACE, width=s)
    labels = (
        ("VOICE", _duration(profile.stats.total_voice_seconds)),
        ("SESSIONS", f"{profile.stats.session_count:,}"),
        ("AVG SESSION", _duration(profile.stats.average_session_seconds)),
        ("SOCIAL", f"{round(profile.stats.social_ratio * 100)}%"),
    )
    for index, (title, value) in enumerate(labels):
        x = 58 + index * 275
        box(x, 282, x + 258, 364, 12, theme.PANEL, theme.BORDER)
        label(x + 18, 296, title, 12, 600, theme.MUTED)
        label(x + 18, 318, value, 23, 600, max_width=222)
    box(58, 383, 793, 608, 13, theme.PANEL, theme.BORDER)
    box(811, 383, 1142, 608, 13, theme.PANEL, theme.BORDER)
    label(78, 397, "14-DAY ACTIVITY", 13, 700)
    label(876, 397, "INSIGHTS", 13, 700)
    _chart(d, profile, s, accent)
    for index, (title, value) in enumerate(_insights(profile, identity)):
        y = 428 + index * 57
        label(832, y, title, 11, 600, theme.MUTED)
        label(832, y + 17, value, 19, 600, max_width=285)
    d.line((832 * s, 479 * s, 1121 * s, 479 * s), fill=theme.BORDER, width=s)
    d.line((832 * s, 536 * s, 1121 * s, 536 * s), fill=theme.BORDER, width=s)
    label(58, 622, "Lifetime · 14-day activity", 11, color=theme.MUTED)
    label(927, 622, profile.timezone_label, 11, color=theme.MUTED, max_width=210)
    return image


def _chart(
    d: ImageDraw.ImageDraw, profile: VoiceProfile, s: int, accent: tuple[int, int, int]
) -> None:
    points = profile.days
    usable = [p for p in points if p.usable]
    if not usable or profile.stats.total_voice_seconds == 0:
        message = (
            "No voice history yet"
            if profile.stats.total_voice_seconds == 0
            else "Activity data unavailable"
        )
        d.text(
            (95 * s, 491 * s),
            message,
            font=_font(20, 600, s),
            fill=theme.MUTED,
        )
        d.text(
            (95 * s, 524 * s),
            "Some periods are unavailable",
            font=_font(12, 400, s),
            fill=theme.QUIET,
        )
        return
    maximum = max((p.voice_seconds for p in usable), default=0.0)
    guide = max(3600, ceil(maximum / 3600 / 2) * 2 * 3600)
    left, right, top, bottom = 98, 750, 443, 565
    for step in range(3):
        y = bottom - round((bottom - top) * step / 2)
        d.line((left * s, y * s, right * s, y * s), fill=theme.BORDER, width=s)
        d.text(
            (68 * s, (y - 6) * s),
            f"{round(guide * step / 7200)}h",
            font=_font(10, 400, s),
            fill=theme.QUIET,
        )
    centers = [left + 25 + i * 45 for i in range(14)]
    for index, p in enumerate(points):
        x = centers[index]
        if p.usable:
            height = (
                max(2, round((bottom - top) * p.voice_seconds / guide))
                if p.voice_seconds
                else 0
            )
            if height:
                d.rounded_rectangle(
                    ((x - 12) * s, (bottom - height) * s, (x + 12) * s, bottom * s),
                    radius=3 * s,
                    fill=accent if p.today else theme.QUIET,
                )
        else:
            d.rounded_rectangle(
                ((x - 12) * s, (bottom - 23) * s, (x + 12) * s, bottom * s),
                radius=3 * s,
                outline=theme.QUIET,
                width=s,
            )
            d.line(
                ((x - 9) * s, (bottom - 4) * s, (x + 9) * s, (bottom - 20) * s),
                fill=theme.QUIET,
                width=s,
            )
        if index in (0, 3, 6, 9, 13):
            d.text(
                ((x - 12) * s, 575 * s),
                p.day.strftime("%d %b"),
                font=_font(10, 400, s),
                fill=theme.MUTED,
            )
    _chart_line(d, points, centers, top, bottom, guide, s, accent)
    if any(not p.usable for p in points):
        d.text(
            (500 * s, 399 * s),
            "Some periods are unavailable",
            font=_font(10, 400, s),
            fill=theme.MUTED,
        )


def _chart_line(
    d: ImageDraw.ImageDraw,
    points: tuple[DailyActivityPoint, ...],
    centers: list[int],
    top: int,
    bottom: int,
    guide: int,
    s: int,
    accent: tuple[int, int, int],
) -> None:
    """Draw only consecutive known moving-average segments."""
    for before, after in zip(range(13), range(1, 14), strict=True):
        a, b = points[before], points[after]
        if (
            a.moving_average_seconds is not None
            and b.moving_average_seconds is not None
        ):
            y0 = bottom - round((bottom - top) * a.moving_average_seconds / guide)
            y1 = bottom - round((bottom - top) * b.moving_average_seconds / guide)
            d.line(
                (centers[before] * s, y0 * s, centers[after] * s, y1 * s),
                fill=accent,
                width=3 * s,
                joint="curve",
            )


def render_png(profile: VoiceProfile, identity: CardIdentity) -> bytes:
    """Render a crisp 1200x675 PNG from a 2400x1350 working canvas."""
    source = draw_card(profile, identity)
    output = source.resize(theme.SIZE, Image.Resampling.LANCZOS)
    buffer = BytesIO()
    output.save(buffer, format="PNG", optimize=True)
    source.close()
    output.close()
    return buffer.getvalue()
