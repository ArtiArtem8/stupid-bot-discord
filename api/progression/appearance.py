"""Versioned level colours and motion hints, without Discord or XP imports.

Each row starts a band ending before the next row. Colours do not interpolate
between tiers. The final colour saturates at 150+, while levels keep growing.
The palette is presentation data, not an XP or level rule.
"""

from dataclasses import dataclass
from typing import Literal

type MotionHint = Literal[
    "none",
    "border_shift",
    "border_shift+bar_sheen",
    "border_shift+bar_sheen+secondary_line",
    "border_shift+bar_sheen+prismatic_microaccent",
]


def _require_level(level: object) -> None:
    if isinstance(level, bool) or not isinstance(level, int):
        raise TypeError("Level must be an integer")
    if level < 1:
        raise ValueError("Level starts at one")


def _require_color(color: object) -> None:
    if isinstance(color, bool) or not isinstance(color, int):
        raise TypeError("Colour must be a 24-bit integer")
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError("Colour must fit 0x000000..0xFFFFFF")


@dataclass(frozen=True, slots=True)
class LevelColorBand:
    """A minimum-level breakpoint with tier, RGB colour and motion hint."""

    minimum_level: int
    tier: str
    color: int
    motion: MotionHint = "none"

    def __post_init__(self) -> None:
        """Reject invalid levels, missing tier keys and non-RGB values."""
        _require_level(self.minimum_level)
        if not self.tier.strip():
            raise ValueError("A colour band needs a tier key")
        _require_color(self.color)


# Approximate cumulative social hours at 1200 XP/h, in band order.
# Solo at 300 XP/h takes 4x longer; default T(L) = 250 * (L - 1) ** 2.
DEFAULT_BANDS = (
    # starter: ~0.0 h.
    LevelColorBand(1, "starter", 0x7F8A98, "none"),
    # uncommon: ~3.3, 10.2 h.
    LevelColorBand(5, "uncommon", 0x4A9A5E, "none"),
    LevelColorBand(8, "uncommon", 0x45B164, "none"),
    # rare: ~16.9, 35.2, 53.3 h.
    LevelColorBand(10, "rare", 0x367ED4, "none"),
    LevelColorBand(14, "rare", 0x2A8EEF, "none"),
    LevelColorBand(17, "rare", 0x28A1FF, "none"),
    # epic: ~75.2, 120.0, 163.3, 200.2 h.
    LevelColorBand(20, "epic", 0x8460D2, "border_shift"),
    LevelColorBand(25, "epic", 0x9965E3, "border_shift"),
    LevelColorBand(29, "epic", 0xAF6CF0, "border_shift"),
    LevelColorBand(32, "epic", 0xC74AF9, "border_shift"),
    # mythic: ~240.8, 285.2, 350.2, 403.3, 440.8 h.
    LevelColorBand(35, "mythic", 0xA646AD, "border_shift"),
    LevelColorBand(38, "mythic", 0xB946B3, "border_shift"),
    LevelColorBand(42, "mythic", 0xCC48B6, "border_shift"),
    LevelColorBand(45, "mythic", 0xDB50B6, "border_shift"),
    LevelColorBand(47, "mythic", 0xE65FB3, "border_shift"),
    # legendary: ~500.2, 585.2, 653.3, 750.0, 826.9, 907.5, 991.9, 1050.2 h.
    LevelColorBand(50, "legendary", 0xB3222B, "border_shift+bar_sheen"),
    LevelColorBand(54, "legendary", 0xC02621, "border_shift+bar_sheen"),
    LevelColorBand(57, "legendary", 0xCC2C05, "border_shift+bar_sheen"),
    LevelColorBand(61, "legendary", 0xD24100, "border_shift+bar_sheen"),
    LevelColorBand(64, "legendary", 0xD75300, "border_shift+bar_sheen"),
    LevelColorBand(67, "legendary", 0xDD6300, "border_shift+bar_sheen"),
    LevelColorBand(70, "legendary", 0xE17200, "border_shift+bar_sheen"),
    LevelColorBand(72, "legendary", 0xE68100, "border_shift+bar_sheen"),
    # ascendant: ~1140.8, 1235.2, 1333.3, 1400.8, 1505.2,
    # 1576.9, 1687.5, 1763.3, 1880.2, 1960.2 h.
    LevelColorBand(75, "ascendant", 0x966C00, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(78, "ascendant", 0x9B7300, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(81, "ascendant", 0xA17A00, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(83, "ascendant", 0xA68000, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(86, "ascendant", 0xAB8700, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(88, "ascendant", 0xB08E00, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(91, "ascendant", 0xB59600, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(93, "ascendant", 0xB99D00, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(96, "ascendant", 0xBEA400, "border_shift+bar_sheen+secondary_line"),
    LevelColorBand(98, "ascendant", 0xC1AC15, "border_shift+bar_sheen+secondary_line"),
    # transcendent: ~2041.9, 2253.3, 2475.2, 2707.5,
    # 2950.2, 3466.9, 4025.2, 4625.2 h.
    LevelColorBand(
        100, "transcendent", 0x0071C3, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        105, "transcendent", 0x007DBD, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        110, "transcendent", 0x0088BB, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        115, "transcendent", 0x0092BA, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        120, "transcendent", 0x009CBA, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        130, "transcendent", 0x00A8BC, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        140, "transcendent", 0x00B4BE, "border_shift+bar_sheen+prismatic_microaccent"
    ),
    LevelColorBand(
        150, "transcendent", 0x00BFC1, "border_shift+bar_sheen+prismatic_microaccent"
    ),
)


@dataclass(frozen=True, slots=True)
class LevelAppearance:
    """Presentation metadata, not an Embed, role, permission or reward."""

    tier: str
    color: int
    band_minimum_level: int
    palette_version: str
    motion: MotionHint


@dataclass(frozen=True, slots=True)
class LevelAppearancePolicy:
    """Select one stepped colour; palette changes cannot affect levels or XP."""

    bands: tuple[LevelColorBand, ...] = DEFAULT_BANDS
    version: str = "level-colors-v1"

    def __post_init__(self) -> None:
        """Require a complete, ordered set of unambiguous breakpoints."""
        if not self.version.strip():
            raise ValueError("A palette needs a version")
        if not self.bands or self.bands[0].minimum_level != 1:
            raise ValueError("A palette must start at level one")
        levels = tuple(band.minimum_level for band in self.bands)
        if levels != tuple(sorted(set(levels))):
            raise ValueError("Colour breakpoints must be strictly increasing")
        _require_contiguous_tiers(self.bands)

    def for_level(self, level: int) -> LevelAppearance:
        """Select the last reached breakpoint, without wrapping at high levels."""
        _require_level(level)
        band = next(b for b in reversed(self.bands) if level >= b.minimum_level)
        return LevelAppearance(
            band.tier, band.color, band.minimum_level, self.version, band.motion
        )


def _require_contiguous_tiers(bands: tuple[LevelColorBand, ...]) -> None:
    seen: set[str] = set()
    previous = ""
    for band in bands:
        if band.tier != previous:
            if band.tier in seen:
                raise ValueError("A tier cannot reappear after another tier")
            seen.add(band.tier)
            previous = band.tier
