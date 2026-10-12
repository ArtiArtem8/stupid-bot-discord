"""Exact cumulative level thresholds; no voice or presentation dependencies."""

from dataclasses import dataclass
from fractions import Fraction
from math import isqrt
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class LevelProgress:
    """Progress within one level without discarding fractional lifetime XP."""

    level: int
    total_xp: Fraction
    current_threshold: int
    next_threshold: int

    @property
    def earned(self) -> Fraction:
        """Return XP earned since the current level threshold."""
        return self.total_xp - self.current_threshold

    @property
    def required(self) -> int:
        """Return the full XP cost of the current level step."""
        return self.next_threshold - self.current_threshold

    @property
    def remaining(self) -> Fraction:
        """Return the exact XP distance to the next level."""
        return self.next_threshold - self.total_xp

    @property
    def ratio(self) -> Fraction:
        """Return fractional progress in [0, 1), suitable for later formatting."""
        return self.earned / self.required


@dataclass(frozen=True, slots=True, kw_only=True)
class LevelPolicy:
    """Cumulative quadratic-linear level curve starting at level one."""

    quadratic_coefficient: int = 200
    linear_coefficient: int = 2050
    version: ClassVar[str] = "quadratic-linear-v3"

    def __post_init__(self) -> None:
        """Require a positive quadratic and nonnegative linear coefficient."""
        _require_positive_integer(self.quadratic_coefficient)
        _require_nonnegative_integer(self.linear_coefficient)

    def threshold(self, level: int) -> int:
        """Return cumulative XP needed for a positive integer level."""
        _require_positive_integer(level)
        x = level - 1
        return self.quadratic_coefficient * x * x + self.linear_coefficient * x

    def level_for(self, xp: Fraction | int) -> int:
        """Find the exact level, including at very large threshold boundaries."""
        amount = _xp_amount(xp)
        # Thresholds are integers, so fractional XP cannot cross a new boundary.
        whole_xp = amount.numerator // amount.denominator
        discriminant = (
            self.linear_coefficient * self.linear_coefficient
            + 4 * self.quadratic_coefficient * whole_xp
        )
        x = (isqrt(discriminant) - self.linear_coefficient) // (
            2 * self.quadratic_coefficient
        )
        return x + 1

    def progress(self, xp: Fraction | int) -> LevelProgress:
        """Return level and progress values; there is no upper level cap."""
        amount = _xp_amount(xp)
        level = self.level_for(amount)
        return LevelProgress(
            level, amount, self.threshold(level), self.threshold(level + 1)
        )


def _require_positive_integer(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Levels and quadratic coefficient must be integers")
    if value < 1:
        raise ValueError("Levels and quadratic coefficient must be positive")


def _require_nonnegative_integer(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Linear coefficient must be an integer")
    if value < 0:
        raise ValueError("Linear coefficient must be nonnegative")


def _xp_amount(value: object) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (Fraction, int)):
        raise TypeError("XP must be an int or Fraction; do not convert via float")
    amount = Fraction(value)
    if amount < 0:
        raise ValueError("XP must be nonnegative")
    return amount
