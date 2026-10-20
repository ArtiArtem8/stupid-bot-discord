"""Timezone display labels and exact UTC offset-transition boundaries."""

from datetime import datetime, timedelta, tzinfo

# IANA's Etc/GMT names use POSIX signs, opposite to everyday UTC notation.
_FIXED_OFFSET_LABELS = {
    f"Etc/GMT{-offset:+d}": f"UTC{offset:+d}" for offset in range(-12, 15) if offset
}


def timezone_label(name: str) -> str:
    """Format an Etc/GMT offset for display; preserve other IANA identifiers.

    This only changes the label, never the timezone used for calendar arithmetic.
    """
    return _FIXED_OFFSET_LABELS.get(name, name)


def find_offset_transition(
    lower: datetime, upper: datetime, timezone: tzinfo
) -> datetime:
    """Locate the first microsecond with a changed offset in a UTC interval.

    The caller must provide ordered, aware UTC bounds with different offsets
    in timezone and exactly one transition in (lower, upper]. Bisection keeps
    the work logarithmic in the interval duration without rounding the change
    to a whole hour or minute. The returned datetime is in UTC.
    """
    offset = lower.astimezone(timezone).utcoffset()
    while upper - lower > timedelta(microseconds=1):
        middle = lower + (upper - lower) // 2
        if middle.astimezone(timezone).utcoffset() == offset:
            lower = middle
        else:
            upper = middle
    return upper
