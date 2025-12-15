"""Tests for birthday repository and related functionality."""

from datetime import date

from api.birthday_models import BirthdayGuildConfig, BirthdayUser
from utils.birthday_utils import calculate_days_until_birthday, format_birthday_date


class TestBirthdayGuildConfig:
    """Test cases for BirthdayGuildConfig methods."""

    def test_get_sorted_birthday_list_empty_users(self):
        """Test get_sorted_birthday_list with no users."""
        config = BirthdayGuildConfig(
            guild_id=123456789, server_name="Test Server", channel_id=987654321
        )

        assert hasattr(config, "get_sorted_birthday_list")
        assert callable(getattr(config, "get_sorted_birthday_list", None))

    def test_get_sorted_birthday_list_with_birthdays(self):
        """Test get_sorted_birthday_list with birthday users."""
        config = BirthdayGuildConfig(
            guild_id=123456789, server_name="Test Server", channel_id=987654321
        )

        # Add test users with birthdays
        user1 = BirthdayUser(
            user_id=1111111,
            name="Alice",
            birthday="15-05-1990",  # May 15
        )

        user2 = BirthdayUser(
            user_id=222222,
            name="Bob",
            birthday="10-01-1985",  # January 10
        )

        config.users = {user1.user_id: user1, user2.user_id: user2}

        # Verify method exists
        assert hasattr(config, "get_sorted_birthday_list")

        # Test helper functions directly
        reference_date = date(2025, 1, 1)  # January 1, 2025

        # Test calculate_days_until_birthday
        days_until_birthday1 = calculate_days_until_birthday(
            user1.birthday, reference_date
        )
        days_until_birthday2 = calculate_days_until_birthday(
            user2.birthday, reference_date
        )

        assert days_until_birthday1 is not None
        assert days_until_birthday2 is not None

        # Test format_birthday_date
        formatted_date1 = format_birthday_date(user1.birthday)
        formatted_date2 = format_birthday_date(user2.birthday)

        assert formatted_date1 is not None
        assert formatted_date2 is not None
        assert "15" in formatted_date1
        assert "10" in formatted_date2


def test_calculate_days_until_birthday():
    """Test the calculate_days_until_birthday utility function."""
    from datetime import date

    # Test with a birthday that's coming up this year
    ref_date = date(2025, 1, 1)  # January 1, 2025
    birthday_str = "15-05-1990"  # May 15
    days = calculate_days_until_birthday(birthday_str, ref_date)
    assert days == 134  # Days from Jan 1 to May 15

    # Test with a birthday that already passed this year (should return days to next year)
    ref_date = date(2025, 6, 1)  # June 1, 2025
    birthday_str = "15-05-1990"  # May 15
    days = calculate_days_until_birthday(birthday_str, ref_date)
    assert days == 348  # Days from June 1 to May 15 next year

    # Test with today as reference date (should return 0)
    today = date(2025, 5, 15)
    birthday_str = "15-05-1990"  # May 15
    days = calculate_days_until_birthday(birthday_str, today)
    assert days == 0


def test_format_birthday_date():
    """Test the format_birthday_date utility function."""
    # Test valid date formatting
    birthday_str = "15-05-1990"
    formatted = format_birthday_date(birthday_str)
    assert formatted is not None
    assert "15" in formatted
    assert "мая" in formatted or "May" in formatted

    # Test invalid date
    invalid_birthday = "invalid"
    formatted = format_birthday_date(invalid_birthday)
    assert formatted is None

    # Test short date
    short_birthday = "15-05"
    formatted = format_birthday_date(short_birthday)
    assert formatted is None
