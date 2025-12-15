"""Utility functions for birthday processing."""

from datetime import date, datetime

import config
from resources import MONTH_NAMES_RU


def is_leap(year: int) -> bool:
    """Return True for leap years, False for non-leap years."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _get_safe_birthday(year: int, original: date) -> date:
    """Returns the birthday for a specific year, handling Feb 29 edge cases."""
    if original.month == 2 and original.day == 29 and not is_leap(year):
        return date(year, 2, 28)
    return date(year, original.month, original.day)


def is_birthday_today(birthday_str: str, today: date) -> bool:
    """Check if the given birthday string matches today's date."""
    if not birthday_str:
        return False

    try:
        original_bday = datetime.strptime(birthday_str, config.DATE_FORMAT).date()
        effective_bday = _get_safe_birthday(today.year, original_bday)

        return (effective_bday.month == today.month) and (
            effective_bday.day == today.day
        )
    except ValueError:
        return False


def calculate_days_until_birthday(
    birthday_str: str, reference_date: date
) -> int | None:
    """Calculate days until the next birthday from a reference date.

    Handles leap years: If born on Feb 29, the birthday is treated as
    Feb 28 in non-leap years.

    Args:
        birthday_str: Birthday string in DD-MM-YYYY format
        reference_date: Reference date to calculate from

    Returns:
        Number of days until the birthday, or None if invalid

    """
    if not birthday_str:
        return None

    try:
        birthday_original = datetime.strptime(birthday_str, config.DATE_FORMAT).date()
        this_year_birthday = _get_safe_birthday(reference_date.year, birthday_original)
        if this_year_birthday >= reference_date:
            return (this_year_birthday - reference_date).days
        next_year_birthday = _get_safe_birthday(
            reference_date.year + 1, birthday_original
        )
        return (next_year_birthday - reference_date).days
    except ValueError:
        return None


def format_birthday_date(birthday_str: str) -> str | None:
    """Format a birthday date string to a more readable format.

    Args:
        birthday_str: Birthday string in DD-MM-YYYY format

    Returns:
        Formatted birthday string (e.g., "15 марта") or None if invalid

    """
    if not birthday_str:
        return None

    try:
        birthday = datetime.strptime(birthday_str, config.DATE_FORMAT).date()

        if birthday.month not in MONTH_NAMES_RU:
            return None

        return f"{birthday.day} {MONTH_NAMES_RU[birthday.month]}"
    except ValueError:
        return None
