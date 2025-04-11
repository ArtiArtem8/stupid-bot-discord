def get_russian_word(n, singular, few, many):
    """
    Returns the correct Russian word form based on the number n.
    Rules:
      - If the last digit is 1 and n is not 11 -> singular
      - If the last digit is 2-4 and n is not 12-14 -> few
      - Else -> many
    """
    if n % 10 == 1 and n % 100 != 11:
        return singular
    elif n % 10 in [2, 3, 4] and not (12 <= n % 100 <= 14):
        return few
    else:
        return many


def format_time_russian(total_seconds: int, depth: int = 2) -> str:
    """
    Format total seconds as a human-readable string in Russian (genitive case),
    including years, days, hours, minutes, and seconds.

    Rounding thresholds:
      - If seconds are within 5 seconds of a full minute, round minutes up.
      - If minutes equal 60, increment hours.
      - If hours equal 24, increment days.
      - If days equal 365, increment years.

    The `depth` parameter specifies how many of the largest nonzero units to display.
    For example, with depth=2, a total corresponding to 1 год, 135 дней, and smaller units will show
    only "1 год и 135 дней". If depth is None, all nonzero units are shown.
    """
    total_seconds = int(total_seconds)

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
