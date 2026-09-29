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


@dataclass(frozen=True, slots=True)
class LevelPolicy:
    """Use T(level) = coefficient * (level - 1)**2, starting at level one."""

    coefficient: int = 250
    version: ClassVar[str] = "quadratic-v2"

    def __post_init__(self) -> None:
        """Require a strictly positive integer scale."""
        _require_positive_integer(self.coefficient)

    def threshold(self, level: int) -> int:
        """Return cumulative XP needed for a positive integer level."""
        _require_positive_integer(level)
        return self.coefficient * (level - 1) ** 2

    def level_for(self, xp: Fraction | int) -> int:
        """Find the exact level, including at very large threshold boundaries."""
        amount = _xp_amount(xp)
        return isqrt(amount // self.coefficient) + 1

    def progress(self, xp: Fraction | int) -> LevelProgress:
        """Return level and progress values; there is no upper level cap."""
        amount = _xp_amount(xp)
        level = self.level_for(amount)
        return LevelProgress(
            level, amount, self.threshold(level), self.threshold(level + 1)
        )


def _require_positive_integer(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Levels and their coefficient must be integers")
    if value < 1:
        raise ValueError("Levels and their coefficient must be positive")


def _xp_amount(value: object) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (Fraction, int)):
        raise TypeError("XP must be an int or Fraction; do not convert via float")
    amount = Fraction(value)
    if amount < 0:
        raise ValueError("XP must be nonnegative")
    return amount
