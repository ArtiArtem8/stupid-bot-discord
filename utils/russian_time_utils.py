def get_russian_word(n: int, singular: str, few: str, many: str) -> str:
    """Returns the correct Russian word form based on the number n.
    Rules:
      - If the last digit is 1 and n is not 11 -> singular
      - If the last digit is 2-4 and n is not 12-14 -> few
      - Else -> many.
    """
    if n % 10 == 1 and n % 100 != 11:
        return singular
    elif n % 10 in [2, 3, 4] and not (12 <= n % 100 <= 14):
        return few
    else:
        return many


def format_time_russian(total_seconds: int, depth: int | None = 2) -> str:
    """Formats a duration in seconds into a human-readable Russian string using
    genitive case for time units (years, days, hours, minutes, seconds).
    Provides approximate, reader-friendly durations by rounding values.

    Rounding rules (applied sequentially):
      - Seconds ≥ 55: Round up minutes (set seconds = 0)
      - Minutes ≥ 59: Round up hours (set minutes = 0)
      - Hours ≥ 23: Round up days (set hours = 0)
      - Days ≥ 364: Round up years (set days = 0)
    Rounding cascades - a rounded unit may trigger rounding in the next higher unit.

    Output customization:
      - `depth` controls how many time units to display (default=2):
          • depth=2: Show max of 2 largest non-zero units (e.g. "2 часа и 45 минут")
          • depth=None: Show all non-zero units
      - Zero values are hidden except when duration is 0 ("0 секунд")
      - Units always display from largest to smallest (years → seconds)

    Designed for readability over precision. Examples:
      3599 seconds → "1 час и 0 минут"  (minutes rounded to 60)
      31535999 seconds → "1 год"         (days rounded to 365)
      90 seconds → "1 минута и 30 секунд"
    """
    SEC_PER_MIN = 60
    SEC_PER_HOUR = 3600
    SEC_PER_DAY = 86400
    SEC_PER_YEAR = 31536000  # 365 days

    MINUTE_ROUND_THRESHOLD = 5
    HOUR_ROUND_THRESHOLD = 60
    DAY_ROUND_THRESHOLD = 3600
    YEAR_ROUND_THRESHOLD = 86400

    years, remainder = divmod(total_seconds, SEC_PER_YEAR)
    days, remainder = divmod(remainder, SEC_PER_DAY)
    hours, remainder = divmod(remainder, SEC_PER_HOUR)
    minutes, seconds = divmod(remainder, SEC_PER_MIN)

    if seconds >= SEC_PER_MIN - MINUTE_ROUND_THRESHOLD:
        minutes += 1
        seconds = 0
    if minutes >= 60 - HOUR_ROUND_THRESHOLD / SEC_PER_MIN:
        hours += 1
        minutes = 0
    if hours >= 24 - DAY_ROUND_THRESHOLD / SEC_PER_HOUR:
        days += 1
        hours = 0
    if days >= 365 - YEAR_ROUND_THRESHOLD / SEC_PER_DAY:
        years += 1
        days = 0

    words_map = {
        "years": ("год", "года", "лет"),
        "days": ("день", "дня", "дней"),
        "hours": ("час", "часа", "часов"),
        "minutes": ("минуту", "минуты", "минут"),
        "seconds": ("секунду", "секунды", "секунд"),
    }

    time_units = {
        4: (years, words_map["years"]),
        3: (days, words_map["days"]),
        2: (hours, words_map["hours"]),
        1: (minutes, words_map["minutes"]),
        0: (seconds, words_map["seconds"]),
    }

    if depth is not None:
        time_units = {
            k: v
            for k, v in time_units.items()
            if v[0] > 0 or (k == 0 and not any(v[0] for v in time_units.values()))
        }
        max_key = max(time_units.keys())
        time_units = {k: v for k, v in time_units.items() if k > max_key - depth}

    parts = [
        f"{value} {get_russian_word(value, *words)}"
        for _, (value, words) in sorted(time_units.items(), reverse=True)
    ]

    return (
        ", ".join(parts[:-1]) + " и " + parts[-1]
        if len(parts) > 1
        else " и ".join(parts)
    )


if __name__ == "__main__":
    print(format_time_russian(-864685))
    print(format_time_russian(0, depth=None))
    print(format_time_russian(1))
    print(format_time_russian(60))
    print(format_time_russian(3600))
    print(format_time_russian(86400))
    print(format_time_russian(31536000))
    print(format_time_russian(31536000, None))
    print(format_time_russian(3599, depth=10))
    print(format_time_russian(31535999))
    print(format_time_russian(31535999, None))
