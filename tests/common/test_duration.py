import unittest

from stupidbot.common.duration import (
    DAY_SECONDS,
    TimeUnit,
    UnitValue,
    calculate_duration,
    format_duration_ru,
)


class TestDurationRounding(unittest.TestCase):
    def test_week_to_day_conversion_keeps_rounded_day_count(self) -> None:
        for days in (7, 8, 9, 12):
            with self.subTest(days=days):
                result = calculate_duration(days * DAY_SECONDS + 23 * 3600, fuzzy=True)

                self.assertEqual(result.parts, (UnitValue(TimeUnit.DAY, days + 1),))
                self.assertTrue(result.is_approximate)
                self.assertFalse(result.is_negative)

    def test_exact_mode_preserves_the_remaining_hours(self) -> None:
        result = calculate_duration(8 * DAY_SECONDS + 23 * 3600)

        self.assertEqual(
            result.parts,
            (UnitValue(TimeUnit.DAY, 8), UnitValue(TimeUnit.HOUR, 23)),
        )

    def test_negative_duration_preserves_rounding_and_sign(self) -> None:
        result = format_duration_ru(
            -(8 * DAY_SECONDS + 23 * 3600), fuzzy=True, use_tilde=True
        )

        self.assertEqual(result, "-~9 дней")
