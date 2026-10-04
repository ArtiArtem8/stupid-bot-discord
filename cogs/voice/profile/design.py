"""Bind read-model values to a designer-owned SVG. No XP or journal logic."""

from __future__ import annotations

import base64
import copy
import math
import re
import unicodedata
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final

# Non-parsing tree/serialization APIs only; parsing always uses defusedxml.
from xml.etree.ElementTree import Element, ElementTree, register_namespace  # nosec B405

import regex
from defusedxml.ElementTree import fromstring
from PIL import Image, ImageOps, UnidentifiedImageError

from api.progression.appearance import TIER_ORDER, LevelAppearance, LevelTier
from api.voice.profile.model import VoiceProfile
from cogs.voice.profile.raster import Box, NativeRasterizer
from cogs.voice.profile.theme import TIER_EMBLEM
from utils.json_types import JsonObject, is_json_object
from utils.json_utils import get_json

SVG: Final = "http://www.w3.org/2000/svg"
XLINK: Final = "http://www.w3.org/1999/xlink"
INKSCAPE: Final = "http://www.inkscape.org/namespaces/inkscape"
register_namespace("", SVG)
register_namespace("xlink", XLINK)
register_namespace("inkscape", INKSCAPE)
ASSETS: Final = Path(__file__).with_name("assets")
REQUIRED: Final = frozenset(
    {
        "static-layer",
        "effects-layer",
        "card-shell",
        "avatar-outline",
        "user-avatar",
        "avatar-fallback",
        "guild-avatar",
        "guild-fallback",
        "display-name",
        "guild-name",
        "tier-name",
        "level-value",
        "total-xp",
        "emblem-art",
        "emblem-area",
        "progress-track",
        "progress-fill",
        "progress-label",
        "voice-value",
        "sessions-value",
        "timezone-label",
        "clip-card",
    }
)


@dataclass(frozen=True, slots=True)
class CardIdentity:
    """Display labels and bounded CDN bytes; avatars may contain animated GIF."""

    display_name: str
    guild_name: str
    avatar_bytes: bytes | None = None
    guild_icon_png: bytes | None = None


@dataclass(frozen=True, slots=True)
class Star:
    identifier: str
    offset: float
    box: Box


@dataclass(frozen=True, slots=True)
class BoundDesign:
    """Validated SVG layers and measured anchors in the declared pixel space."""

    svg: bytes
    static_svg: bytes
    stars_svg: bytes
    boxes: dict[str, Box]
    stars: tuple[Star, ...]
    tokens: dict[str, str]
    tier: LevelTier
    width: int
    height: int
    warnings: tuple[str, ...]


def json_object(value: object) -> JsonObject:
    """Validate JSON mappings before interpreting artwork settings."""
    if not is_json_object(value):
        raise ValueError("Expected a JSON object")
    return value


def document_bytes(root: Element) -> bytes:
    """Serialize an SVG tree with explicit UTF-8 XML metadata."""
    output = BytesIO()
    ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output.getvalue()


def style(element: Element, property_name: str, value: str) -> None:
    """Respect Inkscape's inline styles without replacing unrelated placement."""
    properties: dict[str, str] = {}
    for part in element.get("style", "").split(";"):
        if ":" in part:
            key, item = part.split(":", 1)
            properties[key.strip()] = item.strip()
    properties[property_name] = value
    element.set("style", ";".join(f"{key}:{item}" for key, item in properties.items()))
    element.set(property_name, value)


def property_value(element: Element, key: str, default: str) -> str:
    """Resolve inline style before its presentation attribute."""
    properties = dict(
        part.split(":", 1)
        for part in element.get("style", "").split(";")
        if ":" in part
    )
    return properties.get(key, element.get(key, default)).strip()


def replace_text(element: Element, text: str) -> None:
    """Replace a declared text slot while preserving its authored position."""
    # Inkscape may wrap editable text in tspans. Copy its positioning to the
    # text root, then replace only this declared value slot, not nearby artwork.
    for child in list(element):
        if child.tag == f"{{{SVG}}}tspan":
            for attribute in ("x", "y"):
                if element.get(attribute) is None and child.get(attribute) is not None:
                    element.set(attribute, child.attrib[attribute])
        element.remove(child)
    element.text = text


def safe_label(value: str, *, limit: int = 256) -> str:
    """Strip controls and bound graphemes without breaking emoji sequences."""
    normalized = unicodedata.normalize("NFC", value)
    # Preserve ZWJ/variation selectors/combining marks. Strip control and bidi
    # override characters, not every character that Python calls nonprintable.
    cleaned = "".join(
        " " if char in "\r\n\t" else char
        for char in normalized
        if unicodedata.category(char) != "Cc" or char in "\r\n\t"
        if ord(char) not in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
    ).strip()
    clusters = regex.findall(r"\X", cleaned)
    return "".join(clusters[:limit]) or "Unknown"


def normalized_png(data: bytes | None, *, size: int = 256) -> bytes | None:
    """Bound decompression, normalize first frame, strip metadata and fix EXIF."""
    if data is None:
        return None
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Image exceeds the 4 MiB compressed budget")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.width * source.height > 16_000_000:
                    raise ValueError("Image exceeds the 16 megapixel budget")
                source.seek(0)
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image = ImageOps.fit(
                    image, (size, size), method=Image.Resampling.LANCZOS
                )
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise ValueError("Unsupported or damaged avatar") from error
    output = BytesIO()
    image.save(output, "PNG", compress_level=3)
    return output.getvalue()


def check_template(
    data: bytes, required: frozenset[str] = REQUIRED
) -> tuple[Element, dict[str, Element]]:
    """Validate trusted editable templates before native rendering."""
    if len(data) > 2_000_000:
        raise ValueError("SVG template exceeds 2 MiB")
    root = fromstring(data, forbid_dtd=True)
    if root.tag != f"{{{SVG}}}svg":
        raise ValueError("Template must have an SVG root")
    nodes: dict[str, Element] = {}
    prohibited = {
        "script",
        "foreignObject",
        "animate",
        "animateTransform",
        "set",
        "filter",
        "style",
    }
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in prohibited:
            raise ValueError(f"Unsupported SVG element: {local}")
        identifier = element.get("id")
        if identifier:
            if identifier in nodes:
                raise ValueError(f"Duplicate SVG id: {identifier}")
            nodes[identifier] = element
        _check_resources(element)
    missing = required - nodes.keys()
    if missing:
        raise ValueError("Missing SVG IDs: " + ", ".join(sorted(missing)))
    return root, nodes


def _check_resources(element: Element) -> None:
    for key, value in element.attrib.items():
        if key.startswith("on"):
            raise ValueError("SVG event handlers are not permitted")
        if key.rsplit("}", 1)[-1] == "href" and not (
            value.startswith(("#", "data:image/png;base64,"))
        ):
            raise ValueError("SVG resources must be local fragment IDs or inline PNGs")
        for match in re.finditer(r"url\((.*?)\)", value):
            if not match.group(1).strip(" '\"").startswith("#"):
                raise ValueError("External SVG resources are not permitted")


def _pixels(value: str) -> int:
    number = float(value.removesuffix("px"))
    if (
        not math.isfinite(number)
        or not number.is_integer()
        or not 256 <= number <= 1920
    ):
        raise ValueError("Canvas dimensions must be whole pixels between 256 and 1920")
    return int(number)


def theme_tokens(path: Path, appearance: LevelAppearance) -> dict[str, str]:
    """Load shared semantic colors, including the exact level-band accent."""
    raw = json_object(get_json(path))
    tiers = json_object(json_object(raw)["tiers"])
    values = json_object(tiers[appearance.tier.value])
    result: dict[str, str] = {"accent": f"#{appearance.color:06x}"}
    for key, value in values.items():
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"Invalid color token: {key}")
        result[key] = value
    return result


def fit_texts(
    root: Element, nodes: dict[str, Element], raster: NativeRasterizer
) -> dict[str, Box]:
    """Fit authored text budgets without crossing their minimum font size."""
    targets = [node for node in nodes.values() if node.get("data-width") is not None]
    for _ in range(6):
        boxes = raster.query(document_bytes(root))
        changed = False
        for node in targets:
            identifier = node.attrib["id"]
            box = boxes.get(identifier)
            maximum = float(node.attrib["data-width"])
            if maximum <= 0 or not math.isfinite(maximum):
                raise ValueError(f"Invalid text budget: {identifier}")
            if box is None or box.width <= maximum + 0.25:
                continue
            size = float(property_value(node, "font-size", "24").removesuffix("px"))
            minimum = float(node.get("data-min-size", str(size)))
            proposed = max(minimum, math.floor(size * maximum / box.width * 0.98))
            if proposed < size:
                style(node, "font-size", str(proposed))
            else:
                clusters = regex.findall(r"\X", node.text or "")
                keep = max(
                    0,
                    min(
                        len(clusters) - 2, int(len(clusters) * maximum / box.width) - 2
                    ),
                )
                replace_text(node, "".join(clusters[:keep]).rstrip("…") + "…")
            changed = True
        if not changed:
            return boxes
    raise ValueError("Text could not fit the declared SVG budgets after six passes")


def bind_design(
    profile: VoiceProfile,
    identity: CardIdentity,
    template: Path,
    raster: NativeRasterizer,
) -> BoundDesign:
    """Bind and measure approved artwork without changing domain policy."""
    original = template.read_bytes()
    root, nodes = check_template(original)
    for index, node in enumerate(root.iter()):
        if node.tag == f"{{{SVG}}}text" and not node.get("id"):
            node.set("id", f"fixed-label-{index}")
            nodes[node.attrib["id"]] = node
    width, height = _pixels(root.attrib["width"]), _pixels(root.attrib["height"])
    if root.get("viewBox") != f"0 0 {width} {height}":
        raise ValueError(
            "viewBox must use the same pixel coordinate space as the canvas"
        )
    tokens = theme_tokens(template.parent / "themes.json", profile.appearance)
    apply_tokens(root, tokens)
    _bind_metrics(nodes, profile, identity)
    notes = _bind_images(nodes, identity)
    # Bind only the fill length; preserve the designer's placement transform.
    track = nodes["progress-track"]
    fill = nodes["progress-fill"]
    for attribute in ("x", "y", "height", "rx"):
        fill.set(attribute, track.get(attribute, "0"))
    fill.set("width", str(float(track.attrib["width"]) * profile.progress_ratio))
    fill.set("transform", track.get("transform", ""))
    if profile.progress_ratio == 0:
        style(fill, "display", "none")
    bind_emblem(nodes, profile.appearance, tokens)
    active_stars = _activate_stars(nodes, profile)
    boxes = fit_texts(root, nodes, raster)
    stars = tuple(
        Star(
            node.attrib["id"],
            float(node.get("data-offset", "0")),
            boxes[node.attrib["id"]],
        )
        for node in active_stars
        if node.attrib["id"] in boxes
    )
    full = document_bytes(root)
    static_svg, stars_svg = _split_layers(root)
    return BoundDesign(
        full,
        static_svg,
        stars_svg,
        boxes,
        stars,
        tokens,
        profile.appearance.tier,
        width,
        height,
        tuple(notes),
    )


def _bind_metrics(
    nodes: dict[str, Element], profile: VoiceProfile, identity: CardIdentity
) -> None:
    if (
        not math.isfinite(profile.progress_ratio)
        or not 0 <= profile.progress_ratio <= 1
    ):
        raise ValueError("Profile progress must be finite and in [0, 1]")
    if (
        profile.level < 1
        or profile.total_voice_seconds < 0
        or profile.session_count < 0
    ):
        raise ValueError("Negative profile metrics are not valid")
    values = {
        "display-name": safe_label(identity.display_name),
        "guild-name": safe_label(identity.guild_name),
        "tier-name": profile.appearance.tier.value.upper(),
        "level-value": str(profile.level),
        "total-xp": f"{profile.total_xp:,} XP",
        "progress-label": (
            f"{profile.level_earned_xp:,} / {profile.level_required_xp:,} XP"
        ),
        "voice-value": format_duration(profile.total_voice_seconds),
        "sessions-value": f"{profile.session_count:,}",
        "timezone-label": safe_label(profile.timezone_label, limit=64),
    }
    for key, value in values.items():
        replace_text(nodes[key], value)
    label_clusters = regex.findall(r"\X", values["display-name"])
    replace_text(nodes["avatar-fallback"], "".join(label_clusters[:2]).upper())


def _bind_images(nodes: dict[str, Element], identity: CardIdentity) -> tuple[str, ...]:
    notes: list[str] = []
    for identifier, fallback, data in (
        ("user-avatar", "avatar-fallback", identity.avatar_bytes),
        ("guild-avatar", "guild-fallback", identity.guild_icon_png),
    ):
        try:
            png = normalized_png(data)
        except ValueError:
            png = None
            notes.append(f"{identifier}: invalid image replaced by a placeholder")
        if png:
            nodes[identifier].set(
                f"{{{XLINK}}}href",
                "data:image/png;base64," + base64.b64encode(png).decode(),
            )
            style(nodes[fallback], "display", "none")
        else:
            style(nodes[identifier], "display", "none")
    return tuple(notes)


def bind_emblem(
    nodes: dict[str, Element], appearance: LevelAppearance, tokens: dict[str, str]
) -> None:
    """Place shared tier artwork inside the authored emblem transform."""
    emblem = fromstring(
        (ASSETS / f"emblem-{TIER_EMBLEM[appearance.tier]}.svg").read_bytes(),
        forbid_dtd=True,
    )
    group = nodes["emblem-art"]
    for child in list(group):
        group.remove(child)
    for child in emblem:
        item = copy.deepcopy(child)
        if item.get("class") in ("emblem-cut", "emblem-detail"):
            style(item, "fill", "none")
            style(item, "stroke", tokens["bright"])
            style(item, "stroke-width", "2.1")
            style(item, "stroke-linecap", "round")
            style(item, "stroke-linejoin", "round")
        elif item.get("class") == "emblem-fill":
            style(item, "fill", tokens["bright"])
        elif item.get("class") == "emblem-core":
            style(item, "fill", tokens["text"])
        group.append(item)


def _activate_stars(nodes: dict[str, Element], profile: VoiceProfile) -> list[Element]:
    active_stars: list[Element] = []
    tier_index = TIER_ORDER.index(profile.appearance.tier)
    for node in nodes.values():
        if node.get("data-fx") == "star":
            minimum = LevelTier(node.get("data-min-tier", "epic"))
            if TIER_ORDER.index(minimum) <= tier_index:
                active_stars.append(node)
            else:
                style(node, "display", "none")
    return active_stars


def apply_tokens(root: Element, tokens: dict[str, str]) -> None:
    """Bind semantic fills and strokes without modifying placement."""
    for node in root.iter():
        for attribute in ("fill", "stroke"):
            token = node.get(f"data-{attribute}")
            if token:
                style(node, attribute, tokens[token])


def _split_layers(root: Element) -> tuple[bytes, bytes]:
    static_root = copy.deepcopy(root)
    star_root = copy.deepcopy(root)
    for item in static_root.iter():
        if item.get("id") == "effects-layer":
            style(item, "display", "none")
    for item in star_root.iter():
        if item.get("id") == "static-layer":
            style(item, "display", "none")
    return document_bytes(static_root), document_bytes(star_root)


def format_duration(seconds: float, *, compact: bool = False) -> str:
    """Format elapsed time; optionally abbreviate very large hour totals."""
    if 0 < seconds < 60:
        return "<1m"
    minutes = int(seconds // 60)
    if compact and minutes >= 60_000:
        hours = minutes / 60
        if hours >= 1_000_000:
            return f"{hours / 1_000_000:.1f}M h"
        return f"{hours / 1000:.1f}k h"
    return f"{minutes // 60:,}h {minutes % 60:02}m"
