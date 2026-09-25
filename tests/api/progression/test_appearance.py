"""Behavioural checks for fixed tier features and color boundaries."""

import unittest
from itertools import combinations

from api.progression.appearance import (
    TIER_FEATURES,
    TIER_ORDER,
    AppearanceFeature,
    LevelAppearancePolicy,
    LevelColorBand,
    LevelTier,
)
from api.progression.levels import LevelPolicy

EXPECTED_BANDS = (
    (1, LevelTier.STARTER, 0x7F8A98),
    (5, LevelTier.UNCOMMON, 0x4A9A5E),
    (8, LevelTier.UNCOMMON, 0x45B164),
    (10, LevelTier.RARE, 0x367ED4),
    (14, LevelTier.RARE, 0x2A8EEF),
    (17, LevelTier.RARE, 0x28A1FF),
    (20, LevelTier.EPIC, 0x8460D2),
    (25, LevelTier.EPIC, 0x9965E3),
    (29, LevelTier.EPIC, 0xAF6CF0),
    (32, LevelTier.EPIC, 0xC74AF9),
    (35, LevelTier.MYTHIC, 0xA646AD),
    (38, LevelTier.MYTHIC, 0xB946B3),
    (42, LevelTier.MYTHIC, 0xCC48B6),
    (45, LevelTier.MYTHIC, 0xDB50B6),
    (47, LevelTier.MYTHIC, 0xE65FB3),
    (50, LevelTier.LEGENDARY, 0xB3222B),
    (54, LevelTier.LEGENDARY, 0xC02621),
    (57, LevelTier.LEGENDARY, 0xCC2C05),
    (61, LevelTier.LEGENDARY, 0xD24100),
    (64, LevelTier.LEGENDARY, 0xD75300),
    (67, LevelTier.LEGENDARY, 0xDD6300),
    (70, LevelTier.LEGENDARY, 0xE17200),
    (72, LevelTier.LEGENDARY, 0xE68100),
    (75, LevelTier.ASCENDANT, 0x966C00),
    (78, LevelTier.ASCENDANT, 0x9B7300),
    (81, LevelTier.ASCENDANT, 0xA17A00),
    (83, LevelTier.ASCENDANT, 0xA68000),
    (86, LevelTier.ASCENDANT, 0xAB8700),
    (88, LevelTier.ASCENDANT, 0xB08E00),
    (91, LevelTier.ASCENDANT, 0xB59600),
    (93, LevelTier.ASCENDANT, 0xB99D00),
    (96, LevelTier.ASCENDANT, 0xBEA400),
    (98, LevelTier.ASCENDANT, 0xC1AC15),
    (100, LevelTier.TRANSCENDENT, 0x0071C3),
    (105, LevelTier.TRANSCENDENT, 0x007DBD),
    (110, LevelTier.TRANSCENDENT, 0x0088BB),
    (115, LevelTier.TRANSCENDENT, 0x0092BA),
    (120, LevelTier.TRANSCENDENT, 0x009CBA),
    (130, LevelTier.TRANSCENDENT, 0x00A8BC),
    (140, LevelTier.TRANSCENDENT, 0x00B4BE),
    (150, LevelTier.TRANSCENDENT, 0x00BFC1),
)

EXPECTED_TIER_FEATURES = {
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


class TestAppearance(unittest.TestCase):
    def test_feature_bits_are_independent(self) -> None:
        bits = (
            AppearanceFeature.BORDER_MOTION,
            AppearanceFeature.PROGRESS_SHEEN,
            AppearanceFeature.SECONDARY_ACCENT,
            AppearanceFeature.PRISMATIC_ACCENT,
        )
        self.assertEqual(tuple(bit.value for bit in bits), (1, 2, 4, 8))
        for left, right in combinations(bits, 2):
            with self.subTest(left=left, right=right):
                self.assertEqual(left & right, AppearanceFeature.NONE)

    def test_tier_order_and_feature_composition(self) -> None:
        self.assertEqual(tuple(TIER_ORDER), tuple(LevelTier))
        self.assertEqual(dict(TIER_FEATURES), EXPECTED_TIER_FEATURES)
        self.assertNotIn(
            AppearanceFeature.SECONDARY_ACCENT,
            TIER_FEATURES[LevelTier.TRANSCENDENT],
        )

    def test_every_band_start_and_end_matches_design(self) -> None:
        policy = LevelAppearancePolicy()
        for index, (level, tier, color) in enumerate(EXPECTED_BANDS):
            with self.subTest(level=level):
                result = policy.for_level(level)
                self.assertEqual(
                    (
                        result.band_minimum_level,
                        result.tier,
                        result.color,
                        result.features,
                        result.palette_version,
                    ),
                    (
                        level,
                        tier,
                        color,
                        EXPECTED_TIER_FEATURES[tier],
                        "level-colors-v1",
                    ),
                )
                if index + 1 < len(EXPECTED_BANDS):
                    next_level = EXPECTED_BANDS[index + 1][0]
                    self.assertEqual(policy.for_level(next_level - 1), result)
                    self.assertNotEqual(policy.for_level(next_level).color, color)

    def test_last_band_saturates_without_level_cap(self) -> None:
        policy = LevelAppearancePolicy()
        self.assertEqual(policy.for_level(150), policy.for_level(10**20))

    def test_custom_palette_can_change_colors_and_breakpoints(self) -> None:
        level = LevelPolicy().level_for(4100)
        palette = (
            LevelColorBand(1, LevelTier.STARTER, 0x123456),
            LevelColorBand(7, LevelTier.UNCOMMON, 0x654321),
        )
        policy = LevelAppearancePolicy(palette, "custom-palette")
        self.assertEqual(level, 5)
        self.assertEqual(policy.for_level(level).tier, LevelTier.STARTER)
        self.assertEqual(policy.for_level(6).tier, LevelTier.STARTER)
        self.assertEqual(policy.for_level(7).color, 0x654321)
        self.assertEqual(policy.for_level(7).features, AppearanceFeature.NONE)
        self.assertEqual(policy.for_level(7).palette_version, "custom-palette")
        self.assertEqual(LevelPolicy().level_for(4100), level)
        self.assertEqual(
            LevelAppearancePolicy().for_level(level).tier, LevelTier.UNCOMMON
        )

    def test_band_validation_and_fixed_tier_names(self) -> None:
        for color in (-1, 0x1000000):
            with self.subTest(color=color), self.assertRaises(ValueError):
                LevelColorBand(1, LevelTier.STARTER, color)
        with self.assertRaises(ValueError):
            LevelColorBand(0, LevelTier.STARTER, 0)
        with self.assertRaises(ValueError):
            LevelTier("invented")

    def test_palette_requires_ordered_known_tiers(self) -> None:
        for bands in (
            (),
            (LevelColorBand(2, LevelTier.STARTER, 0),),
            (
                LevelColorBand(1, LevelTier.STARTER, 0),
                LevelColorBand(1, LevelTier.RARE, 1),
            ),
            (
                LevelColorBand(1, LevelTier.RARE, 0),
                LevelColorBand(2, LevelTier.STARTER, 1),
            ),
            (
                LevelColorBand(1, LevelTier.STARTER, 0),
                LevelColorBand(2, LevelTier.UNCOMMON, 1),
                LevelColorBand(3, LevelTier.STARTER, 2),
            ),
        ):
            with self.subTest(bands=bands), self.assertRaises(ValueError):
                LevelAppearancePolicy(bands)

    def test_invalid_level_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LevelAppearancePolicy().for_level(0)
        with self.assertRaises(TypeError):
            LevelAppearancePolicy().for_level(True)
