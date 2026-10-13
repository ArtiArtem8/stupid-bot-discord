"""Display fixed-offset IANA keys using conventional UTC signs."""

# IANA's Etc/GMT names use POSIX signs, opposite to everyday UTC notation.
_FIXED_OFFSET_LABELS = {
    f"Etc/GMT{-offset:+d}": f"UTC{offset:+d}" for offset in range(-12, 15) if offset
}


def timezone_label(name: str) -> str:
    """Format an Etc/GMT offset for display; preserve other IANA identifiers.

    This only changes the label, never the timezone used for calendar arithmetic.
    """
    return _FIXED_OFFSET_LABELS.get(name, name)
