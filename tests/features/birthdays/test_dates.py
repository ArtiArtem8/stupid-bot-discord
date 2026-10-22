"""Tests for birthday utility helpers."""

import unittest
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from stupidbot.features.birthdays.dates import (
    day_start,
    is_birthday_today,
    next_celebration,
)
from stupidbot.features.birthdays.presentation import format_birthday_date


class TestNextCelebration(unittest.TestCase):
    def test_today_counts_as_next_celebration(self) -> None:
        self.assertEqual(
            next_celebration(date(1990, 12, 12), date(2025, 12, 12)),
            date(2025, 12, 12),
        )

    def test_passed_birthday_moves_to_next_year(self) -> None:
        self.assertEqual(
            next_celebration(date(2000, 1, 1), date(2025, 6, 15)), date(2026, 1, 1)
        )

    def test_leap_day_uses_feb_28_only_in_non_leap_years(self) -> None:
        leap = date(2000, 2, 29)
        self.assertEqual(next_celebration(leap, date(2025, 1, 1)), date(2025, 2, 28))
        self.assertEqual(next_celebration(leap, date(2024, 2, 28)), date(2024, 2, 29))
        self.assertEqual(next_celebration(leap, date(2025, 3, 1)), date(2026, 2, 28))


class TestDayStart(unittest.TestCase):
    def test_returns_local_midnight_in_utc(self) -> None:
        self.assertEqual(
            day_start(date(2026, 10, 6), ZoneInfo("Europe/Moscow")),
            datetime(2026, 10, 5, 21, tzinfo=UTC),
        )

    def test_skipped_midnight_uses_first_existing_moment_of_day(self) -> None:
        # Clocks in Havana jump from 00:00 to 01:00 on this date.
        zone = ZoneInfo("America/Havana")
        start = day_start(date(2026, 3, 8), zone)
        self.assertEqual(start, datetime(2026, 3, 8, 5, tzinfo=UTC))
        self.assertEqual(start.astimezone(zone).date(), date(2026, 3, 8))
        self.assertEqual(
            datetime.fromtimestamp(start.timestamp() - 1, zone).date(),
            date(2026, 3, 7),
        )

    def test_ambiguous_midnight_uses_first_occurrence(self) -> None:
        # Clocks in Havana fall back from 01:00 to 00:00 on this date.
        start = day_start(date(2026, 11, 1), ZoneInfo("America/Havana"))
        self.assertEqual(start, datetime(2026, 11, 1, 4, tzinfo=UTC))


class TestFormatBirthdayDate(unittest.TestCase):
    def test_format_valid_date(self) -> None:
        birthday = date(1990, 3, 15)

        result = format_birthday_date(birthday)

        self.assertEqual(result, "15 марта")

    def test_format_single_digit_day(self) -> None:
        birthday = date(2000, 5, 1)

        result = format_birthday_date(birthday)

        self.assertEqual(result, "1 мая")

    def test_format_all_months(self) -> None:
        expected_months = {
            "01": "января",
            "02": "февраля",
            "03": "марта",
            "04": "апреля",
            "05": "мая",
            "06": "июня",
            "07": "июля",
            "08": "августа",
            "09": "сентября",
            "10": "октября",
            "11": "ноября",
            "12": "декабря",
        }

        for month, month_name in expected_months.items():
            with self.subTest(month=month):
                birthday = date(2000, int(month), 15)
                result = format_birthday_date(birthday)
                self.assertEqual(result, f"15 {month_name}")

    def test_format_leap_year_date(self) -> None:
        birthday = date(2000, 2, 29)

        result = format_birthday_date(birthday)

        self.assertEqual(result, "29 февраля")


class TestIsBirthdayToday(unittest.TestCase):
    def test_normal_birthday_match(self) -> None:
        bday = "15-05-1990"
        today = date(2025, 5, 15)
        self.assertTrue(is_birthday_today(bday, today))

    def test_normal_birthday_mismatch(self) -> None:
        bday = "15-05-1990"
        today = date(2025, 5, 16)
        self.assertFalse(is_birthday_today(bday, today))

    def test_leap_birthday_in_leap_year_match(self) -> None:
        bday = "29-02-2000"
        today = date(2024, 2, 29)
        self.assertTrue(is_birthday_today(bday, today))

    def test_leap_birthday_in_leap_year_mismatch(self) -> None:
        bday = "29-02-2000"
        today = date(2024, 2, 28)
        self.assertFalse(is_birthday_today(bday, today))

    def test_leap_birthday_in_non_leap_year_match(self) -> None:
        bday = "29-02-2000"
        today = date(2025, 2, 28)
        self.assertTrue(is_birthday_today(bday, today))

    def test_leap_birthday_in_non_leap_year_mismatch(self) -> None:
        bday = "29-02-2000"
        today = date(2025, 3, 1)
        self.assertFalse(is_birthday_today(bday, today))

    def test_invalid_date_returns_false(self) -> None:
        self.assertFalse(is_birthday_today("invalid", date(2025, 1, 1)))
        self.assertFalse(is_birthday_today("", date(2025, 1, 1)))
        self.assertFalse(is_birthday_today("32-01-2000", date(2025, 1, 1)))
