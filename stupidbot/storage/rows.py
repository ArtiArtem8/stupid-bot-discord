"""Typed access to selected SQLAlchemy columns without positional coupling."""

from typing import cast

from sqlalchemy import Column, RowMapping


def column_value[T](row: RowMapping, column: Column[T]) -> T:
    """Read a selected column; SQLAlchemy raises KeyError if it is absent."""
    # SQLAlchemy's mapping API returns Any even for Column[T]. Keep that typing
    # gap here: callers use actual selected columns, never string aliases.
    return cast(T, row[column])
