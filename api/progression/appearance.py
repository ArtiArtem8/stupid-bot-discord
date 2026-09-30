"""Versioned level colors and feature metadata, without Discord or XP imports.

Each row starts a band ending before the next row. Colors do not interpolate
between tiers. The final color saturates at 150+, while levels keep growing.
The palette is presentation data, not an XP or level rule.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Flag, StrEnum
from types import MappingProxyType


class AppearanceFeature(Flag):
    """Independent visual capabilities for a later renderer."""

    NONE = 0
    BORDER_MOTION = 1
    PROGRESS_SHEEN = 2
    SECONDARY_ACCENT = 4
    PRISMATIC_ACCENT = 8


class LevelTier(StrEnum):
    """The eight fixed names of progression grades."""

    STARTER = "starter"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    MYTHIC = "mythic"
    LEGENDARY = "legendary"
    ASCENDANT = "ascendant"
    TRANSCENDENT = "transcendent"


TIER_ORDER = (
    LevelTier.STARTER,
    LevelTier.UNCOMMON,
    LevelTier.RARE,
    LevelTier.EPIC,
    LevelTier.MYTHIC,
    LevelTier.LEGENDARY,
    LevelTier.ASCENDANT,
    LevelTier.TRANSCENDENT,
)

TIER_FEATURES: Mapping[LevelTier, AppearanceFeature] = MappingProxyType(
    {
        LevelTier.STARTER: AppearanceFeature.NONE,
        LevelTier.UNCOMMON: AppearanceFeature.NONE,
        LevelTier.RARE: AppearanceFeature.NONE,
        LevelTier.EPIC: AppearanceFeature.BORDER_MOTION,
        LevelTier.MYTHIC: AppearanceFeature.BORDER_MOTION,
        LevelTier.LEGENDARY: AppearanceFeature.BORDER_MOTION
        | AppearanceFeature.PROGRESS_SHEEN,
        LevelTier.ASCENDANT: AppearanceFeature.BORDER_MOTION
        | AppearanceFeature.PROGRESS_SHEEN
        | AppearanceFeature.SECONDARY_ACCENT,
        LevelTier.TRANSCENDENT: AppearanceFeature.BORDER_MOTION
        | AppearanceFeature.PROGRESS_SHEEN
        | AppearanceFeature.PRISMATIC_ACCENT,
    }
)


def _require_level(level: object) -> None:
    if isinstance(level, bool) or not isinstance(level, int):
        raise TypeError("Level must be an integer")
    if level < 1:
        raise ValueError("Level starts at one")


def _require_color(color: object) -> None:
    if isinstance(color, bool) or not isinstance(color, int):
        raise TypeError("Color must be a 24-bit integer")
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError("Color must fit 0x000000..0xFFFFFF")


@dataclass(frozen=True, slots=True)
class LevelColorBand:
    """A minimum-level breakpoint with a fixed tier and RGB color."""

    minimum_level: int
    tier: LevelTier
    color: int

    def __post_init__(self) -> None:
        """Reject invalid levels and non-RGB values."""
        _require_level(self.minimum_level)
        _require_color(self.color)


# Approximate cumulative social hours at 1200 XP/h, in band order.
# Solo at 300 XP/h takes 4x longer; x = L - 1, T(L) = 200*x*x + 2050*x.
DEFAULT_BANDS = (
    # starter: ~0.0 h.
    LevelColorBand(1, LevelTier.STARTER, 0x7F8A98),
    # uncommon: ~9.5, 20.1 h.
    LevelColorBand(5, LevelTier.UNCOMMON, 0x4A9A5E),
    LevelColorBand(8, LevelTier.UNCOMMON, 0x45B164),
    # rare: ~28.9, 50.4, 70.0 h.
    LevelColorBand(10, LevelTier.RARE, 0x367ED4),
    LevelColorBand(14, LevelTier.RARE, 0x2A8EEF),
    LevelColorBand(17, LevelTier.RARE, 0x28A1FF),
    # epic: ~92.6, 137.0, 178.5, 213.1 h.
    LevelColorBand(20, LevelTier.EPIC, 0x8460D2),
    LevelColorBand(25, LevelTier.EPIC, 0x9965E3),
    LevelColorBand(29, LevelTier.EPIC, 0xAF6CF0),
    LevelColorBand(32, LevelTier.EPIC, 0xC74AF9),
    # mythic: ~250.8, 291.4, 350.2, 397.8, 431.2 h.
    LevelColorBand(35, LevelTier.MYTHIC, 0xA646AD),
    LevelColorBand(38, LevelTier.MYTHIC, 0xB946B3),
    LevelColorBand(42, LevelTier.MYTHIC, 0xCC48B6),
    LevelColorBand(45, LevelTier.MYTHIC, 0xDB50B6),
    LevelColorBand(47, LevelTier.MYTHIC, 0xE65FB3),
    # legendary: ~483.9, 558.7, 618.3, 702.5, 769.1,
    # 838.8, 911.4, 961.5 h.
    LevelColorBand(50, LevelTier.LEGENDARY, 0xB3222B),
    LevelColorBand(54, LevelTier.LEGENDARY, 0xC02621),
    LevelColorBand(57, LevelTier.LEGENDARY, 0xCC2C05),
    LevelColorBand(61, LevelTier.LEGENDARY, 0xD24100),
    LevelColorBand(64, LevelTier.LEGENDARY, 0xD75300),
    LevelColorBand(67, LevelTier.LEGENDARY, 0xDD6300),
    LevelColorBand(70, LevelTier.LEGENDARY, 0xE17200),
    LevelColorBand(72, LevelTier.LEGENDARY, 0xE68100),
    # ascendant: ~1039.1, 1119.7, 1203.3, 1260.8, 1349.4,
    # 1410.1, 1503.8, 1567.8, 1666.5, 1733.9 h.
    LevelColorBand(75, LevelTier.ASCENDANT, 0x966C00),
    LevelColorBand(78, LevelTier.ASCENDANT, 0x9B7300),
    LevelColorBand(81, LevelTier.ASCENDANT, 0xA17A00),
    LevelColorBand(83, LevelTier.ASCENDANT, 0xA68000),
    LevelColorBand(86, LevelTier.ASCENDANT, 0xAB8700),
    LevelColorBand(88, LevelTier.ASCENDANT, 0xB08E00),
    LevelColorBand(91, LevelTier.ASCENDANT, 0xB59600),
    LevelColorBand(93, LevelTier.ASCENDANT, 0xB99D00),
    LevelColorBand(96, LevelTier.ASCENDANT, 0xBEA400),
    LevelColorBand(98, LevelTier.ASCENDANT, 0xC1AC15),
    # transcendent: ~1802.6, 1980.3, 2166.4, 2360.8, 2563.5,
    # 2993.9, 3457.6, 3954.7 h.
    LevelColorBand(100, LevelTier.TRANSCENDENT, 0x0071C3),
    LevelColorBand(105, LevelTier.TRANSCENDENT, 0x007DBD),
    LevelColorBand(110, LevelTier.TRANSCENDENT, 0x0088BB),
    LevelColorBand(115, LevelTier.TRANSCENDENT, 0x0092BA),
    LevelColorBand(120, LevelTier.TRANSCENDENT, 0x009CBA),
    LevelColorBand(130, LevelTier.TRANSCENDENT, 0x00A8BC),
    LevelColorBand(140, LevelTier.TRANSCENDENT, 0x00B4BE),
    LevelColorBand(150, LevelTier.TRANSCENDENT, 0x00BFC1),
)


@dataclass(frozen=True, slots=True)
class LevelAppearance:
    """Presentation metadata, not an Embed, role, permission or reward."""

    tier: LevelTier
    color: int
    band_minimum_level: int
    palette_version: str
    features: AppearanceFeature


@dataclass(frozen=True, slots=True)
class LevelAppearancePolicy:
    """Select one stepped color; palette changes cannot affect levels or XP."""

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
            raise ValueError("Color breakpoints must be strictly increasing")
        _require_tier_order(self.bands)

    def for_level(self, level: int) -> LevelAppearance:
        """Select the last reached breakpoint, without wrapping at high levels."""
        _require_level(level)
        band = next(b for b in reversed(self.bands) if level >= b.minimum_level)
        return LevelAppearance(
            band.tier,
            band.color,
            band.minimum_level,
            self.version,
            TIER_FEATURES[band.tier],
        )


def _require_tier_order(bands: tuple[LevelColorBand, ...]) -> None:
    previous_index = -1
    for band in bands:
        index = TIER_ORDER.index(band.tier)
        if index < previous_index:
            raise ValueError("Palette tiers must follow TIER_ORDER")
        previous_index = index
