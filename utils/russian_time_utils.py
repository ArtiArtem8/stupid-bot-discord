"""Human-friendly Russian duration formatting.

This module converts a duration in seconds into a compact, UX-oriented breakdown
and formats it in Russian with correct nominative counting forms.

Pipeline structure:
1) Decompose seconds into an initial parts list using an adaptive unit set.
2) Apply a sequence of rules that may:
   - rewrite parts (for UX),
   - drop insignificant tail units,
   - accumulate dropped seconds for fuzzy approximation,
   - optionally round up based on the omitted tail.

Public entry points:
- calculate_duration_ru: Returns a structured breakdown.
- format_duration_ru: Convenience wrapper that returns a formatted string.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "DurationBreakdown",
    "DurationUXConfig",
    "Rule",
    "TimeUnit",
    "UnitForms",
    "UnitValue",
    "calculate_duration",
    "format_breakdown_ru",
    "format_duration_ru",
    "plural_ru",
]

DAY_SECONDS: Final[int] = 86_400
"""Number of seconds in one day."""

_SECONDS_IN_MINUTE: Final[int] = 60
"""Base conversion constant: seconds per minute."""

_MINUTES_IN_HOUR: Final[int] = 60
"""Base conversion constant: minutes per hour."""

_HOURS_IN_DAY: Final[int] = 24
"""Base conversion constant: hours per day."""

_DAYS_IN_WEEK: Final[int] = 7
"""Base conversion constant: days per week."""

_MONTHS_IN_YEAR: Final[int] = 12
"""Base conversion constant: months per year."""


class TimeUnit(IntEnum):
    """Ordered time units used for breakdown and formatting.

    Lower numeric value means a larger unit. This allows simple comparisons.
    For example, `unit <= TimeUnit.HOUR` means "unit is HOUR or larger".
    """

    YEAR = 0
    MONTH = 1
    WEEK = 2
    DAY = 3
    HOUR = 4
    MINUTE = 5
    SECOND = 6


@dataclass(frozen=True, slots=True)
class UnitForms:
    """Russian nominative counting forms: 1, 2-4, 5+."""

    one: str
    few: str
    many: str


FORMS: Final[Mapping[TimeUnit, UnitForms]] = MappingProxyType(
    {
        TimeUnit.YEAR: UnitForms("год", "года", "лет"),
        TimeUnit.MONTH: UnitForms("месяц", "месяца", "месяцев"),
        TimeUnit.WEEK: UnitForms("неделю", "недели", "недель"),
        TimeUnit.DAY: UnitForms("день", "дня", "дней"),
        TimeUnit.HOUR: UnitForms("час", "часа", "часов"),
        TimeUnit.MINUTE: UnitForms("минуту", "минуты", "минут"),
        TimeUnit.SECOND: UnitForms("секунду", "секунды", "секунд"),
    }
)
"""Russian nominative counting forms for each unit."""


_DEFAULT_MIN_SIGNIFICANT_SECONDS: Final[Mapping[TimeUnit, int]] = MappingProxyType(
    {
        TimeUnit.SECOND: 2,
        TimeUnit.MINUTE: 2 * _SECONDS_IN_MINUTE,
        TimeUnit.HOUR: 2 * _SECONDS_IN_MINUTE * _MINUTES_IN_HOUR,
        TimeUnit.DAY: 2 * DAY_SECONDS,
    }
)
"""Absolute significance floors for small tail units."""


@dataclass(frozen=True, slots=True)
class DurationUXConfig:
    """Configuration knobs for duration breakdown and display.

    This class stores UX thresholds, not algorithmic state.

    Attributes:
        depth_noise_keep_at_least: If depth is below this value, noise filtering
            becomes more aggressive.
        noise_ratio: Relative noise threshold between adjacent kept units.
        round_up_ratio: Tail fraction required to round up the last kept unit.
        round_up_ratio_year: Like round_up_ratio, but for years.
        month_min_days: Switch to month or year scale for durations at least this
            many days.
        week_min_days: Minimum duration (in days) to allow week-based outputs.
        week_max_days: Maximum duration (in days) to allow week-based outputs.
        month_days: Approximate month length used for month calculations.
        year_days: Approximate year length used for year calculations.
        hide_seconds_if_unit_ge: Hide seconds if output includes this unit or
            larger.
        long_scale_min_unit: If months or years are present, do not display units
            smaller than this by default.
        min_significant_seconds_by_unit: Absolute floors that can force keeping a
            unit even when it is small relative to the previous unit.

    """

    depth_noise_keep_at_least: int = 3
    noise_ratio: float = 0.08

    round_up_ratio: float = 0.90
    round_up_ratio_year: float = 0.985

    month_min_days: int = 90
    week_min_days: int = 7
    week_max_days: int = 29

    month_days: float = 30.5
    year_days: float = 365.25

    hide_seconds_if_unit_ge: TimeUnit = TimeUnit.HOUR
    long_scale_min_unit: TimeUnit = TimeUnit.DAY

    min_significant_seconds_by_unit: Mapping[TimeUnit, int] = (
        _DEFAULT_MIN_SIGNIFICANT_SECONDS
    )

    @property
    def day_seconds(self) -> int:
        """Return the number of seconds in one day."""
        return DAY_SECONDS

    @property
    def month_seconds(self) -> int:
        """Return the approximate number of seconds in a month."""
        return int(self.month_days * DAY_SECONDS)

    @property
    def year_seconds(self) -> int:
        """Return the approximate number of seconds in a year."""
        return int(self.year_days * DAY_SECONDS)

    def min_significant_seconds(self, unit: TimeUnit) -> int:
        """Return the absolute significance floor for a unit."""
        return self.min_significant_seconds_by_unit.get(unit, 0)


@dataclass(frozen=True, slots=True)
class UnitValue:
    """A single unit-value pair, for example 3 days."""

    unit: TimeUnit
    value: int


@dataclass(frozen=True, slots=True)
class DurationBreakdown:
    """Structured duration breakdown suitable for formatting."""

    parts: tuple[UnitValue, ...]
    is_approximate: bool
    is_negative: bool

    @property
    def is_zero(self) -> bool:
        return not self.parts or (
            len(self.parts) == 1
            and self.parts[0].unit == TimeUnit.SECOND
            and self.parts[0].value == 0
        )


def plural_ru(value: int, forms: UnitForms) -> str:
    """Choose a Russian nominative counting form for a number.

    Args:
        value: The value to inflect.
        forms: A (one, few, many) triplet.

    Returns:
        The correct Russian nominative counting form.

    """
    n = abs(value)
    if n % 10 == 1 and n % 100 != 11:
        return forms.one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return forms.few
    return forms.many


def _seconds_table(cfg: DurationUXConfig) -> dict[TimeUnit, int]:
    return {
        TimeUnit.SECOND: 1,
        TimeUnit.MINUTE: _SECONDS_IN_MINUTE,
        TimeUnit.HOUR: _SECONDS_IN_MINUTE * _MINUTES_IN_HOUR,
        TimeUnit.DAY: DAY_SECONDS,
        TimeUnit.WEEK: _DAYS_IN_WEEK * DAY_SECONDS,
        TimeUnit.MONTH: cfg.month_seconds,
        TimeUnit.YEAR: cfg.year_seconds,
    }


def _allowed_units(total_seconds: int, cfg: DurationUXConfig) -> tuple[TimeUnit, ...]:
    if total_seconds >= cfg.month_min_days * DAY_SECONDS:
        return (
            TimeUnit.YEAR,
            TimeUnit.MONTH,
            TimeUnit.DAY,
            TimeUnit.HOUR,
            TimeUnit.MINUTE,
            TimeUnit.SECOND,
        )
    if (
        cfg.week_min_days * DAY_SECONDS
        <= total_seconds
        <= cfg.week_max_days * DAY_SECONDS
    ):
        return (
            TimeUnit.WEEK,
            TimeUnit.DAY,
            TimeUnit.HOUR,
            TimeUnit.MINUTE,
            TimeUnit.SECOND,
        )
    return (TimeUnit.DAY, TimeUnit.HOUR, TimeUnit.MINUTE, TimeUnit.SECOND)


def _contains_unit_ge(parts: Sequence[UnitValue], threshold: TimeUnit) -> bool:
    return any(p.unit <= threshold for p in parts)


def _drop_below_unit(
    parts: Sequence[UnitValue],
    sec: Mapping[TimeUnit, int],
    min_unit: TimeUnit,
) -> tuple[list[UnitValue], int]:
    kept: list[UnitValue] = []
    dropped = 0
    for p in parts:
        if p.unit > min_unit:
            dropped += p.value * sec[p.unit]
        else:
            kept.append(p)
    return kept, int(dropped)


def _normalize(parts: Sequence[UnitValue], *, with_weeks: bool) -> list[UnitValue]:
    """Normalize overflows for stable bases (60, 24, 7, 12).

    Note:
        Day to week normalization is applied only when the breakdown already
        includes weeks, to preserve the "7 days" style when weeks are not used.

    """
    values: dict[TimeUnit, int] = {}
    for p in parts:
        if p.value:
            values[p.unit] = values.get(p.unit, 0) + p.value

    chain: list[tuple[TimeUnit, TimeUnit, int]] = [
        (TimeUnit.SECOND, TimeUnit.MINUTE, _SECONDS_IN_MINUTE),
        (TimeUnit.MINUTE, TimeUnit.HOUR, _MINUTES_IN_HOUR),
        (TimeUnit.HOUR, TimeUnit.DAY, _HOURS_IN_DAY),
    ]
    if with_weeks:
        chain.append((TimeUnit.DAY, TimeUnit.WEEK, _DAYS_IN_WEEK))
    chain.append((TimeUnit.MONTH, TimeUnit.YEAR, _MONTHS_IN_YEAR))

    for small, large, base in chain:
        v = values.get(small, 0)
        if v >= base:
            carry, rem = divmod(v, base)
            values[small] = rem
            values[large] = values.get(large, 0) + carry

    out = [UnitValue(u, v) for u, v in values.items() if v]
    out.sort(key=lambda p: p.unit)
    return out or [UnitValue(TimeUnit.SECOND, 0)]


def _is_noise(
    prev: UnitValue,
    cur: UnitValue,
    depth: int,
    cfg: DurationUXConfig,
    sec: Mapping[TimeUnit, int],
) -> bool:
    if depth >= cfg.depth_noise_keep_at_least:
        return False

    prev_sec = prev.value * sec[prev.unit]
    cur_sec = cur.value * sec[cur.unit]
    if prev_sec == 0:
        return False

    if (cur_sec / prev_sec) >= cfg.noise_ratio:
        return False

    if cur.unit in (TimeUnit.SECOND, TimeUnit.MINUTE, TimeUnit.HOUR) and prev.unit in (
        TimeUnit.DAY,
        TimeUnit.WEEK,
        TimeUnit.MONTH,
        TimeUnit.YEAR,
    ):
        return True

    if prev.unit is TimeUnit.WEEK and cur.unit is TimeUnit.DAY:
        return False

    return cur_sec < cfg.min_significant_seconds(cur.unit)


def _decompose_units(
    total_seconds: int,
    cfg: DurationUXConfig,
    sec: Mapping[TimeUnit, int],
    allowed: Sequence[TimeUnit],
    *,
    fuzzy: bool,
) -> tuple[list[UnitValue], bool]:
    if total_seconds == 0:
        return [UnitValue(TimeUnit.SECOND, 0)], False

    remainder = total_seconds
    approx = False
    out: list[UnitValue] = []

    for unit in allowed:
        unit_sec = sec[unit]
        value, remainder = divmod(remainder, unit_sec)

        if fuzzy and remainder:
            ratio = (
                cfg.round_up_ratio_year if unit is TimeUnit.YEAR else cfg.round_up_ratio
            )
            if remainder >= int(ratio * unit_sec):
                value += 1
                remainder = 0
                approx = True

        if value:
            out.append(UnitValue(unit, int(value)))

    if not fuzzy:
        return out, approx

    with_weeks = any(p.unit is TimeUnit.WEEK for p in out)
    return _normalize(out, with_weeks=with_weeks), approx


@dataclass(slots=True)
class DurationState:
    """Mutable state processed by pipeline rules.

    Attributes:
        seconds: Absolute input duration in seconds.
        depth: Maximum number of units to keep.
        fuzzy: Whether fuzzy rounding is enabled.
        cfg: UX configuration.
        sec: Seconds-per-unit lookup table computed once per run.
        parts: Current parts list, sorted from larger units to smaller units.
        dropped_seconds: Tail seconds removed by rules.
        is_approximate: Whether the result should be marked as approximate.

    """

    seconds: int
    depth: int
    fuzzy: bool
    cfg: DurationUXConfig
    sec: dict[TimeUnit, int]
    parts: list[UnitValue] = field(default_factory=list)
    dropped_seconds: int = 0
    is_approximate: bool = False


RuleFn = Callable[[DurationState], None]


@dataclass(frozen=True, slots=True)
class Rule:
    """A single rule.

    Attributes:
        name: Stable identifier used for ordering and debugging.
        summary: One-line description for rule overview.
        apply: Function that mutates DurationState in place.

    """

    name: str
    summary: str
    apply: RuleFn


def _rule_week_singularity(st: DurationState) -> None:
    """Avoid '1 week X days'. Prefer pure days for 8..13 days."""
    week = next((p.value for p in st.parts if p.unit is TimeUnit.WEEK), 0)
    day = next((p.value for p in st.parts if p.unit is TimeUnit.DAY), 0)
    if week == 1 and day > 0:
        days_total = st.seconds // st.cfg.day_seconds
        rest = [p for p in st.parts if p.unit not in (TimeUnit.WEEK, TimeUnit.DAY)]
        st.parts = [UnitValue(TimeUnit.DAY, int(days_total)), *rest]
        st.is_approximate = True


def _rule_visibility(st: DurationState) -> None:
    """Apply long-scale and seconds-hiding visibility rules."""
    if any(p.unit in (TimeUnit.YEAR, TimeUnit.MONTH) for p in st.parts):
        st.parts, dropped = _drop_below_unit(
            st.parts,
            st.sec,
            st.cfg.long_scale_min_unit,
        )
        st.dropped_seconds += dropped

    if _contains_unit_ge(st.parts, st.cfg.hide_seconds_if_unit_ge):
        st.parts, dropped = _drop_below_unit(st.parts, st.sec, TimeUnit.MINUTE)
        st.dropped_seconds += dropped


def _rule_depth_and_noise(st: DurationState) -> None:
    """Select parts honoring depth and noise filtering."""
    selected: list[UnitValue] = []
    dropped = 0

    for p in st.parts:
        if not selected:
            selected.append(p)
            continue

        if len(selected) >= st.depth:
            dropped += p.value * st.sec[p.unit]
            continue

        if _is_noise(selected[-1], p, st.depth, st.cfg, st.sec):
            dropped += p.value * st.sec[p.unit]
            continue

        selected.append(p)

    st.parts = selected or [UnitValue(TimeUnit.SECOND, 0)]
    st.dropped_seconds += dropped


def _rule_finalize_fuzzy_rounding(st: DurationState) -> None:
    """Apply tail-aware rounding to the last kept unit in fuzzy mode."""
    if not st.fuzzy or st.dropped_seconds <= 0:
        return

    last = st.parts[-1]
    unit_sec = st.sec[last.unit]
    ratio = (
        st.cfg.round_up_ratio_year
        if last.unit is TimeUnit.YEAR
        else st.cfg.round_up_ratio
    )
    if st.dropped_seconds >= int(ratio * unit_sec):
        bumped = [*st.parts[:-1], UnitValue(last.unit, last.value + 1)]
        with_weeks = any(p.unit is TimeUnit.WEEK for p in bumped)
        st.parts = _normalize(bumped, with_weeks=with_weeks)
        st.is_approximate = True


DEFAULT_RULES: Final[tuple[Rule, ...]] = (
    Rule(
        name="week_singularity",
        summary="Avoid '1 week X days'. Prefer pure days for 8..13 days.",
        apply=_rule_week_singularity,
    ),
    Rule(
        name="visibility",
        summary="Apply long-scale and seconds-hiding visibility rules.",
        apply=_rule_visibility,
    ),
    Rule(
        name="depth_and_noise",
        summary="Select parts honoring depth and noise filtering.",
        apply=_rule_depth_and_noise,
    ),
    Rule(
        name="finalize_fuzzy_rounding",
        summary="Tail-aware rounding and approximation marking in fuzzy mode.",
        apply=_rule_finalize_fuzzy_rounding,
    ),
)
"""Default ordered rule list for the duration pipeline."""


def _apply_rules(st: DurationState, rules: Sequence[Rule]) -> None:
    for rule in rules:
        rule.apply(st)


def calculate_duration(
    total_seconds: int,
    depth: int = 2,
    *,
    fuzzy: bool = False,
    config: DurationUXConfig | None = None,
    rules: Sequence[Rule] = DEFAULT_RULES,
) -> DurationBreakdown:
    """Convert seconds into a UX-friendly duration breakdown.

    Args:
        total_seconds: Duration in seconds. Can be negative.
        depth: Maximum number of units to keep. Must be >= 1.
        fuzzy: If True, enable fuzzy rounding and approximation marking.
        config: Optional configuration override.
        rules: Ordered pipeline rules. Defaults to DEFAULT_RULES.

    Returns:
        A DurationBreakdown that can be formatted or further processed.

    Raises:
        ValueError: If depth is less than 1.

    """
    if depth < 1:
        raise ValueError("depth must be >= 1")

    cfg = config or DurationUXConfig()
    sec = _seconds_table(cfg)

    is_negative = total_seconds < 0
    seconds = abs(total_seconds)

    allowed = _allowed_units(seconds, cfg)
    parts, approx = _decompose_units(seconds, cfg, sec, allowed, fuzzy=fuzzy)

    st = DurationState(
        seconds=seconds,
        depth=depth,
        fuzzy=fuzzy,
        cfg=cfg,
        sec=sec,
        parts=parts,
        dropped_seconds=0,
        is_approximate=approx,
    )

    _apply_rules(st, rules)

    return DurationBreakdown(
        parts=tuple(st.parts),
        is_approximate=st.is_approximate,
        is_negative=is_negative,
    )


def format_breakdown_ru(
    breakdown: DurationBreakdown,
    *,
    use_tilde: bool = False,
) -> str:
    """Format a DurationBreakdown into a Russian human-readable string.

    Args:
        breakdown: A computed duration breakdown.
        use_tilde: If True, prefix approximate results with "~".

    Returns:
        A formatted Russian string, for example "2 дня и 3 часа".

    """
    parts = [f"{p.value} {plural_ru(p.value, FORMS[p.unit])}" for p in breakdown.parts]
    if not parts:
        return f"0 {FORMS[TimeUnit.SECOND].many}"

    text = parts[0] if len(parts) == 1 else (", ".join(parts[:-1]) + " и " + parts[-1])

    prefix = ""
    if breakdown.is_negative and not breakdown.is_zero:
        prefix += "-"
    if use_tilde and breakdown.is_approximate and not breakdown.is_zero:
        prefix += "~"

    return prefix + text


def format_duration_ru(
    total_seconds: int,
    depth: int = 2,
    *,
    fuzzy: bool = False,
    use_tilde: bool = False,
    config: DurationUXConfig | None = None,
) -> str:
    """Convenience wrapper: calculate and format in one call.

    Args:
        total_seconds: Duration in seconds. Can be negative.
        depth: Maximum number of units to keep. Must be >= 1.
        fuzzy: If True, enable fuzzy rounding and approximation marking.
        use_tilde: If True, prefix approximate results with "~".
        config: Optional configuration override.

    Returns:
        A formatted Russian string.

    """
    bd = calculate_duration(total_seconds, depth, fuzzy=fuzzy, config=config)
    return format_breakdown_ru(bd, use_tilde=use_tilde)
