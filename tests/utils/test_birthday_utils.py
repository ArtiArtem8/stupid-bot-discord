"""Tests for birthday utility functions."""

import unittest
from datetime import date

from utils.birthday_utils import calculate_days_until_birthday, format_birthday_date


class TestCalculateDaysUntilBirthday(unittest.TestCase):
    def test_birthday_today(self):
        """Birthday is today - should return 0."""
        reference = date(2025, 12, 12)
        birthday_str = "12-12-1990"

        result = calculate_days_until_birthday(birthday_str, reference)

        self.assertEqual(result, 0)

    def test_birthday_in_future_this_year(self):
        """Birthday hasn't happened yet this year."""
        reference = date(2025, 1, 15)
        birthday_str = "20-03-1995"  # March 20

        result = calculate_days_until_birthday(birthday_str, reference)

        expected = (date(2025, 3, 20) - reference).days
        self.assertEqual(result, expected)
        self.assertEqual(result, 64)  # Jan 15 to March 20

    def test_birthday_already_passed_this_year(self):
        """Birthday already happened - calculate for next year."""
        reference = date(2025, 6, 15)
        birthday_str = "01-01-2000"  # January 1

        result = calculate_days_until_birthday(birthday_str, reference)

        expected = (date(2026, 1, 1) - reference).days
        self.assertEqual(result, expected)
        self.assertEqual(result, 200)

    def test_leap_year_feb_29_birthday(self):
        """Handle Feb 29 birthday in non-leap year."""
        reference = date(2025, 1, 1)  # 2025 is not a leap year
        birthday_str = "29-02-2000"  # Valid leap year birthday

        # Python's date() will raise ValueError for Feb 29 in non-leap year
        # This is expected behavior - the function should return None
        result = calculate_days_until_birthday(birthday_str, reference)
        self.assertIsNone(result)

        # In a leap year reference
        reference_leap = date(2024, 1, 1)
        result_leap = calculate_days_until_birthday(birthday_str, reference_leap)
        self.assertEqual(result_leap, 59)  # Jan 1 to Feb 29, 2024

    def test_invalid_date_format(self):
        """Invalid date formats should return None."""
        reference = date(2025, 12, 12)

        test_cases = [
            "1-1-2000",  # Missing leading zeros
            "32-01-2000",  # Invalid day
            "01-13-2000",  # Invalid month
            "2000-12-12",  # Wrong format (YYYY-MM-DD)
            "12/12/2000",  # Wrong separator
            "not-a-date",  # Garbage
            "12-12",  # Incomplete
            "",  # Empty string
        ]

        for birthday_str in test_cases:
            with self.subTest(birthday_str=birthday_str):
                result = calculate_days_until_birthday(birthday_str, reference)
                self.assertIsNone(result)

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
            "32-01-2000",  # Invalid day
            "01-13-2000",  # Invalid month
            "29-02-2023",  # Invalid Feb 29 in non-leap year
            "not-a-date",  # Garbage
            "2000-12-12",  # Wrong format
            "12/12/2000",  # Wrong separator
            "",  # Empty string
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
