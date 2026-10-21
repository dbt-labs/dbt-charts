"""Data-aware variable input type refinement.

Stage: RENDER (post-query); lives in compile so the compile-time restated-default check can ask what inference would pick
Purpose: Inspect resolved options data to refine the compile-time input type.

The compile-time detector (detect_variable_input_type) handles YAML-structural
signals: options → select, min/max → slider, bool default → checkbox. This module
adds the second phase: data-aware refinement that runs after options queries
execute — mirroring how chart type auto-detection works at render time.

Detection priority: date > boolean > numeric-slider > unchanged.
"""

import math
from datetime import date
from typing import TYPE_CHECKING, NamedTuple

from dbt_charts.core.compile.models.variable.authored import VariableInputType

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.variable.authored import Variable

# Thresholds
_SLIDER_MAX_DISTINCT = 200  # Don't offer slider for huge option sets
_SLIDER_MAX_RANGE_STEPS = 1000  # Range / step must be <= this

_BOOLEAN_PAIRS = frozenset(
    {
        frozenset({"true", "false"}),
        frozenset({"yes", "no"}),
        # Note: {"0", "1"} is ambiguous — could be numeric range.
        # We treat it as boolean here since a 0/1 slider isn't useful.
        frozenset({"0", "1"}),
    }
)


class RefinedType(NamedTuple):
    """Result of data-aware input type refinement."""

    input_type: VariableInputType
    slider_min: int | float | None = None
    slider_max: int | float | None = None
    slider_step: int | float | None = None


def refine_input_type_from_data(
    var_def: "Variable",
    option_values: list[str],
) -> RefinedType:
    """Refine a variable's input type based on its resolved option values.

    Only refines when the compile-time type was auto-detected (input_auto_detected).
    Explicit author-set types are never overridden. Does not mutate var_def.

    Args:
        var_def: Variable definition (read-only).
        option_values: Resolved distinct option values as strings.

    Returns:
        RefinedType with the input type and optional slider bounds.
    """
    current = var_def.input
    if not var_def.input_auto_detected:
        return RefinedType(current)

    if not option_values:
        return RefinedType(current)

    # --- Type-based detection (strongest signal) ---

    if _looks_like_dates(option_values):
        return RefinedType("datepicker")

    if _looks_like_booleans(option_values):
        return RefinedType("checkbox")

    # A slider always holds a value; options read from a query (a column's
    # values) stay a select so "All" can mean no bound.
    query_backed = var_def.options is not None and bool(var_def.options.query)
    if len(option_values) <= _SLIDER_MAX_DISTINCT and not query_backed:
        parsed = _try_parse_numbers(option_values)
        if parsed is not None:
            min_val, max_val = min(parsed), max(parsed)
            val_range = max_val - min_val
            if val_range > 0:
                all_ints = all(v.is_integer() for v in parsed)
                step = 1 if all_ints else round(val_range / 100, 6)
                if val_range / step <= _SLIDER_MAX_RANGE_STEPS:
                    return RefinedType(
                        "slider",
                        slider_min=int(min_val) if all_ints else min_val,
                        slider_max=int(max_val) if all_ints else max_val,
                        slider_step=int(step) if all_ints else step,
                    )

    # No type-based refinement matched — keep compile-time type (select).
    return RefinedType(current)


def _looks_like_dates(values: list[str]) -> bool:
    """True if >= 80% of values parse as ISO dates via datetime.date.fromisoformat.

    Single-value lists return False — a single option is too little signal.
    """
    if len(values) < 2:
        return False
    matches = 0
    for v in values:
        try:
            # Require full YYYY-MM-DD (10 chars minimum)
            if len(v) < 10:
                continue
            date.fromisoformat(v[:10])
            # Reject if there's trailing content that isn't a time separator
            if len(v) > 10 and v[10] not in ("T", " ", "\t"):
                continue
            matches += 1
        except ValueError:
            pass
    return matches / len(values) >= 0.8


def _looks_like_booleans(values: list[str]) -> bool:
    """True if values are exactly a known boolean pair."""
    if len(values) != 2:
        return False
    pair = frozenset(v.lower().strip() for v in values)
    return pair in _BOOLEAN_PAIRS


def _try_parse_numbers(values: list[str]) -> list[float] | None:
    """Try to parse all values as finite numbers. Returns None if any fail."""
    parsed: list[float] = []
    for v in values:
        try:
            n = float(v)
            if not math.isfinite(n):
                return None
            parsed.append(n)
        except (ValueError, TypeError):
            return None
    return parsed
