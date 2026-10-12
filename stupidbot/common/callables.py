"""Helpers for introspecting callable-like objects."""


def callable_name(func: object) -> str:
    """Return a stable diagnostic name for a callable-like object.

    Qualified names are preferred so nested functions and methods remain
    distinguishable. Objects without callable metadata fall back to their type
    name; this helper does not require the object to be callable.
    """
    name = getattr(func, "__qualname__", None)
    if isinstance(name, str):
        return name
    name = getattr(func, "__name__", None)
    if isinstance(name, str):
        return name
    return type(func).__name__
