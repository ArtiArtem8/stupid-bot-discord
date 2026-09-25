"""Behavioural checks for the versioned level appearance palette."""

import unittest
from dataclasses import replace

from api.progression.appearance import (
    DEFAULT_BANDS,
    LevelAppearancePolicy,
    LevelColorBand,
)
from api.progression.levels import LevelPolicy

EXPECTED_BANDS = (
    (1, "starter", 0x7F8A98, "none"),
    (5, "uncommon", 0x4A9A5E, "none"),
    (8, "uncommon", 0x45B164, "none"),
    (10, "rare", 0x367ED4, "none"),
    (14, "rare", 0x2A8EEF, "none"),
    (17, "rare", 0x28A1FF, "none"),
    (20, "epic", 0x8460D2, "border_shift"),
    (25, "epic", 0x9965E3, "border_shift"),
    (29, "epic", 0xAF6CF0, "border_shift"),
    (32, "epic", 0xC74AF9, "border_shift"),
    (35, "mythic", 0xA646AD, "border_shift"),
    (38, "mythic", 0xB946B3, "border_shift"),
    (42, "mythic", 0xCC48B6, "border_shift"),
    (45, "mythic", 0xDB50B6, "border_shift"),
    (47, "mythic", 0xE65FB3, "border_shift"),
    (50, "legendary", 0xB3222B, "border_shift+bar_sheen"),
    (54, "legendary", 0xC02621, "border_shift+bar_sheen"),
    (57, "legendary", 0xCC2C05, "border_shift+bar_sheen"),
    (61, "legendary", 0xD24100, "border_shift+bar_sheen"),
    (64, "legendary", 0xD75300, "border_shift+bar_sheen"),
    (67, "legendary", 0xDD6300, "border_shift+bar_sheen"),
    (70, "legendary", 0xE17200, "border_shift+bar_sheen"),
    (72, "legendary", 0xE68100, "border_shift+bar_sheen"),
    (75, "ascendant", 0x966C00, "border_shift+bar_sheen+secondary_line"),
    (78, "ascendant", 0x9B7300, "border_shift+bar_sheen+secondary_line"),
    (81, "ascendant", 0xA17A00, "border_shift+bar_sheen+secondary_line"),
    (83, "ascendant", 0xA68000, "border_shift+bar_sheen+secondary_line"),
    (86, "ascendant", 0xAB8700, "border_shift+bar_sheen+secondary_line"),
    (88, "ascendant", 0xB08E00, "border_shift+bar_sheen+secondary_line"),
    (91, "ascendant", 0xB59600, "border_shift+bar_sheen+secondary_line"),
    (93, "ascendant", 0xB99D00, "border_shift+bar_sheen+secondary_line"),
    (96, "ascendant", 0xBEA400, "border_shift+bar_sheen+secondary_line"),
    (98, "ascendant", 0xC1AC15, "border_shift+bar_sheen+secondary_line"),
    (100, "transcendent", 0x0071C3, "border_shift+bar_sheen+prismatic_microaccent"),
    (105, "transcendent", 0x007DBD, "border_shift+bar_sheen+prismatic_microaccent"),
    (110, "transcendent", 0x0088BB, "border_shift+bar_sheen+prismatic_microaccent"),
    (115, "transcendent", 0x0092BA, "border_shift+bar_sheen+prismatic_microaccent"),
    (120, "transcendent", 0x009CBA, "border_shift+bar_sheen+prismatic_microaccent"),
    (130, "transcendent", 0x00A8BC, "border_shift+bar_sheen+prismatic_microaccent"),
    (140, "transcendent", 0x00B4BE, "border_shift+bar_sheen+prismatic_microaccent"),
    (150, "transcendent", 0x00BFC1, "border_shift+bar_sheen+prismatic_microaccent"),
)


class TestAppearance(unittest.TestCase):
    def test_every_band_start_and_end_matches_design(self) -> None:
        policy = LevelAppearancePolicy()
        for index, (level, tier, color, motion) in enumerate(EXPECTED_BANDS):
            with self.subTest(level=level):
                result = policy.for_level(level)
                self.assertEqual(
                    (
                        result.band_minimum_level,
                        result.tier,
                        result.color,
                        result.motion,
                        result.palette_version,
                    ),
                    (level, tier, color, motion, "level-colors-v1"),
                )
                if index + 1 < len(EXPECTED_BANDS):
                    next_level = EXPECTED_BANDS[index + 1][0]
                    self.assertEqual(policy.for_level(next_level - 1), result)
                    self.assertNotEqual(policy.for_level(next_level).color, color)

    def test_last_band_saturates_without_level_cap(self) -> None:
        policy = LevelAppearancePolicy()
        self.assertEqual(policy.for_level(150), policy.for_level(10**20))

    def test_palette_can_change_without_changing_xp_or_level(self) -> None:
        level = LevelPolicy().level_for(4100)
        updated = tuple(replace(band, color=0x123456) for band in DEFAULT_BANDS)
        policy = LevelAppearancePolicy(updated, "custom-palette")
        result = policy.for_level(level)
        self.assertEqual(result.color, 0x123456)
        self.assertEqual(result.tier, "uncommon")
        self.assertEqual(result.motion, "none")
        self.assertEqual(result.palette_version, "custom-palette")
        self.assertEqual(LevelPolicy().level_for(4100), level)

    def test_band_validation(self) -> None:
        for color in (-1, 0x1000000):
            with self.subTest(color=color), self.assertRaises(ValueError):
                LevelColorBand(1, "starter", color)
        with self.assertRaises(ValueError):
            LevelColorBand(0, "starter", 0)
        with self.assertRaises(ValueError):
            LevelColorBand(1, "", 0)

    def test_invalid_palette_order_or_missing_first_level_is_rejected(self) -> None:
        for bands in (
            (),
            (LevelColorBand(2, "starter", 0),),
            (LevelColorBand(1, "starter", 0), LevelColorBand(1, "rare", 1)),
        ):
            with self.subTest(bands=bands), self.assertRaises(ValueError):
                LevelAppearancePolicy(bands)

    def test_tier_cannot_return_after_another_tier(self) -> None:
        with self.assertRaises(ValueError):
            LevelAppearancePolicy(
                (
                    LevelColorBand(1, "a", 0),
                    LevelColorBand(2, "b", 1),
                    LevelColorBand(3, "a", 2),
                )
            )

    def test_invalid_level_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LevelAppearancePolicy().for_level(0)
        with self.assertRaises(TypeError):
            LevelAppearancePolicy().for_level(True)
