"""Birthday calendar rules and storage date normalization."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo

DATE_FORMAT = "%d-%m-%Y"


def format_storage_date(value: date) -> str:
    """Return DD-MM-YYYY with four year digits, independent of platform strftime."""
    return f"{value.day:02d}-{value.month:02d}-{value.year:04d}"


def is_leap(year: int) -> bool:
    """Return True for leap years, False for non-leap years."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _get_safe_birthday(year: int, original: date) -> date:
    """Return one birthday occurrence, mapping Feb. 29 to Feb. 28 when needed."""
    if original.month == 2 and original.day == 29 and not is_leap(year):
        return date(year, 2, 28)
    return date(year, original.month, original.day)


def is_birthday_today(birthday_str: str, today: date) -> bool:
    """Return whether a stored birthday is celebrated on ``today``.

    Stored values use :data:`DATE_FORMAT`. Feb. 29 birthdays are
    celebrated on Feb. 28 in non-leap years. Empty or malformed values return
    ``False`` rather than raising.
    """
    if not birthday_str:
        return False

    try:
        original_bday = datetime.strptime(birthday_str, DATE_FORMAT).date()
        effective_bday = _get_safe_birthday(today.year, original_bday)

        return effective_bday == today
    except ValueError:
        return False


def next_celebration(birthday: date, today: date) -> date:
    """Return the first celebration date on or after ``today``.

    Feb. 29 birthdays are celebrated on Feb. 28 in non-leap years. The birth
    year does not affect the result.
    """
    occurrence = _get_safe_birthday(today.year, birthday)
    if occurrence < today:
        occurrence = _get_safe_birthday(today.year + 1, birthday)
    return occurrence


def day_start(day: date, zone: tzinfo) -> datetime:
    """Return the first existing moment of ``day`` in ``zone`` as UTC.

    ``zoneinfo`` resolves a midnight skipped by a clock change (``fold=0``)
    to the moment the day begins and an ambiguous midnight to its first
    occurrence.
    """
    return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC)


def parse_birthday(date_str: str) -> str:
    """Parse a birthday date string into a standardized format.

    Args:
        date_str: Date in DD-MM-YYYY or YYYY-MM-DD format.

    Returns:
        The date in DD-MM-YYYY format.

    Raises:
        ValueError: If the date string is invalid or does not match either of the
            supported formats.
    """
    for fmt in (DATE_FORMAT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return format_storage_date(dt)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use DD-MM-YYYY or YYYY-MM-DD.")
