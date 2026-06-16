"""Helpers for introspecting callable-like objects."""


def callable_name(func: object) -> str:
    """Return a useful display name for a callable-like object."""
    name = getattr(func, "__qualname__", None)
    if isinstance(name, str):
        return name
    name = getattr(func, "__name__", None)
    if isinstance(name, str):
        return name
    return type(func).__name__
