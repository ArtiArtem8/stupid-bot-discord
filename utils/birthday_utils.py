"""Utility functions for birthday processing."""

from datetime import date, datetime

import config
from resources import MONTH_NAMES_RU


def calculate_days_until_birthday(
    birthday_str: str, reference_date: date
) -> int | None:
    """Calculate days until the next birthday from a reference date.

    Args:
        birthday_str: Birthday string in DD-MM-YYYY format
        reference_date: Reference date to calculate from

    Returns:
        Number of days until the birthday, or None if invalid

    """
    if not birthday_str:
        return None

    try:
        birthday = datetime.strptime(birthday_str, config.DATE_FORMAT).date()
        this_year_birthday = date(reference_date.year, birthday.month, birthday.day)
        if this_year_birthday >= reference_date:
            return (this_year_birthday - reference_date).days
        next_year_birthday = date(reference_date.year + 1, birthday.month, birthday.day)
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
