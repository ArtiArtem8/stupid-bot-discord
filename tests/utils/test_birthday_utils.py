"""Tests for birthday utility helpers.
Covers date parsing, leap-year handling, and birthday checks.
"""

import unittest
from datetime import date

from utils.birthday_utils import (
    calculate_days_until_birthday,
    format_birthday_date,
    is_birthday_today,
)


class TestCalculateDaysUntilBirthday(unittest.TestCase):
    def test_birthday_today(self):
        """Birthday is today - should return 0."""
        reference = date(2025, 12, 12)
        birthday_str = "12-12-1990"

        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertEqual(result, 0)

    def test_leap_year_birthday_today(self):
        """Birthday is today (February 29th) in a leap year - should return 0."""
        reference = date(2025, 2, 28)
        birthday_str = "29-02-1992"

        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertEqual(result, 0)

    def test_leap_year_birthday_on_actual_day(self):
        """Test that leap year birthdays are handled correctly on non-leap years.
        This test ensures that on February 28th, a leap year birthday on February 29th
        is still recognized as being 0 days away.
        """
        reference = date(2025, 2, 28)
        birthday_str = "29-02-1992"

        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertEqual(result, 0)

    def test_leap_year_birthday_wait_for_29th(self):
        """In a leap year, Feb 29 birthday should NOT happen on Feb 28."""
        reference = date(2024, 2, 28)
        birthday_str = "29-02-2000"
        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertEqual(result, 1)

    def test_birthday_in_future_this_year(self):
        """Birthday hasn't happened yet this year."""
        reference = date(2025, 1, 15)
        birthday_str = "20-03-1995"

        result = calculate_days_until_birthday(birthday_str, reference)

        expected = (date(2025, 3, 20) - reference).days
        self.assertEqual(result, expected)
        self.assertEqual(result, 64)

    def test_birthday_already_passed_this_year(self):
        """Birthday already happened - calculate for next year."""
        reference = date(2025, 6, 15)
        birthday_str = "01-01-2000"

        result = calculate_days_until_birthday(birthday_str, reference)

        expected = (date(2026, 1, 1) - reference).days
        self.assertEqual(result, expected)
        self.assertEqual(result, 200)

    def test_leap_year_feb_29_birthday(self):
        """Handle Feb 29 birthday in non-leap year."""
        reference = date(2025, 1, 1)
        birthday_str = "29-02-2000"

        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertEqual(result, 58)

        reference_leap = date(2024, 1, 1)
        result_leap = calculate_days_until_birthday(birthday_str, reference_leap)
        self.assertEqual(result_leap, 59)

    def test_invalid_date_format(self):
        """Invalid date formats should return None."""
        reference = date(2025, 12, 12)

        test_cases = [
            "32-01-2000",
            "01-13-2000",
            "2000-12-12",
            "12/12/2000",
            "not-a-date",
            "12-12",
            "",
        ]

        for birthday_str in test_cases:
            with self.subTest(birthday_str=birthday_str):
                result = calculate_days_until_birthday(birthday_str, reference)
                self.assertIsNone(result, msg=birthday_str)

    def test_none_input(self):
        """None input should return None."""
        reference = date(2025, 12, 12)

        result = calculate_days_until_birthday("", reference)

        self.assertIsNone(result)

    def test_new_year_edge_case(self):
        """Test birthday on Dec 31 when reference is Jan 1."""
        reference = date(2025, 1, 1)
        birthday_str = "31-12-1995"

        result = calculate_days_until_birthday(birthday_str, reference)

        expected = (date(2025, 12, 31) - reference).days
        self.assertEqual(result, expected)
        self.assertEqual(result, 364)

    def test_calculate_days_until_birthday_leap_logic(self):
        """Test calculation of days until birthday with leap logic."""
        bday_str = "29-02-2000"

        ref_date = date(2025, 1, 1)
        days = calculate_days_until_birthday(bday_str, ref_date)
        self.assertEqual(days, 58)
        ref_date_leap = date(2024, 1, 1)
        days_leap = calculate_days_until_birthday(bday_str, ref_date_leap)
        self.assertEqual(days_leap, 59)


class TestFormatBirthdayDate(unittest.TestCase):
    def test_format_valid_date(self):
        """Format a valid birthday date."""
        birthday_str = "15-03-1990"

        result = format_birthday_date(birthday_str)

        self.assertEqual(result, "15 марта")

    def test_format_single_digit_day(self):
        """Format date with single-digit day (with leading zero)."""
        birthday_str = "01-05-2000"

        result = format_birthday_date(birthday_str)

        self.assertEqual(result, "1 мая")

    def test_format_all_months(self):
        """Test formatting for all 12 months."""
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
                birthday_str = f"15-{month}-2000"
                result = format_birthday_date(birthday_str)
                self.assertEqual(result, f"15 {month_name}")

    def test_format_invalid_date(self):
        """Invalid dates should return None."""
        test_cases = [
            "32-01-2000",
            "01-13-2000",
            "29-02-2023",
            "not-a-date",
            "2000-12-12",
            "12/12/2000",
            "",
        ]

        for birthday_str in test_cases:
            with self.subTest(birthday_str=birthday_str):
                result = format_birthday_date(birthday_str)
                self.assertIsNone(result)

    def test_format_leap_year_date(self):
        """Format Feb 29 from a leap year."""
        birthday_str = "29-02-2000"

        result = format_birthday_date(birthday_str)

        self.assertEqual(result, "29 февраля")


class TestIsBirthdayToday(unittest.TestCase):
    """Test cases for is_birthday_today logic."""

    def test_normal_birthday_match(self):
        """Standard birthday matches today."""
        bday = "15-05-1990"
        today = date(2025, 5, 15)
        self.assertTrue(is_birthday_today(bday, today))

    def test_normal_birthday_mismatch(self):
        """Standard birthday does not match today."""
        bday = "15-05-1990"
        today = date(2025, 5, 16)
        self.assertFalse(is_birthday_today(bday, today))

    def test_leap_birthday_in_leap_year_match(self):
        """Born Feb 29, today is Feb 29 (Leap Year) -> Match."""
        bday = "29-02-2000"
        today = date(2024, 2, 29)  # 2024 is leap
        self.assertTrue(is_birthday_today(bday, today))

    def test_leap_birthday_in_leap_year_mismatch(self):
        """Born Feb 29, today is Feb 28 (Leap Year) -> No match."""
        bday = "29-02-2000"
        today = date(2024, 2, 28)
        self.assertFalse(is_birthday_today(bday, today))

    def test_leap_birthday_in_non_leap_year_match(self):
        """Born Feb 29, today is Feb 28 (Non-Leap Year) -> Match."""
        bday = "29-02-2000"
        today = date(2025, 2, 28)  # 2025 is not leap
        self.assertTrue(is_birthday_today(bday, today))

    def test_leap_birthday_in_non_leap_year_mismatch(self):
        """Born Feb 29, today is Mar 1 (Non-Leap Year) -> No match."""
        bday = "29-02-2000"
        today = date(2025, 3, 1)
        self.assertFalse(is_birthday_today(bday, today))

    def test_invalid_date_returns_false(self):
        """Invalid date strings return False safely."""
        self.assertFalse(is_birthday_today("invalid", date(2025, 1, 1)))
        self.assertFalse(is_birthday_today("", date(2025, 1, 1)))
        self.assertFalse(is_birthday_today("32-01-2000", date(2025, 1, 1)))
