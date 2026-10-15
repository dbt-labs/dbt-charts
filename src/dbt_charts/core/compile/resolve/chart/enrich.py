"""Chart enrichment: data-aware column classification and scale heuristics.

Stage: COMPILE
Purpose: classify query result columns and decide the y-axis zero baseline.

Key principle: every auto-decision is overridable by explicit YAML.
Authors override the zero baseline via style.axis_y.scale.continuous.zero.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from dbt_charts.core.utils import is_year_shaped

__all__ = [
    "ColumnProfile",
    "classify_column_type",
    "first_non_null_samples",
    "is_column_discrete_for_bar_orientation",
    "_is_numeric_string",
    "_is_temporal_value",
    "_pick_scale",
]

# Chart types where zero anchoring is optional; the smart-auto heuristic applies.
# Bar and area always extend to zero — both are absolute-magnitude encodings
# (a bar's length and an area's fill both misstate the quantity when truncated),
# so neither is in this set.
_OPTIONAL_ZERO_CHART_TYPES = frozenset({"line", "scatter"})

# Threshold for smart-auto zero extension.  When data_min/data_max <= this value,
# data starts in the bottom 25% of [0, max] and extending to zero adds honest
# context without crushing the data.  Above the threshold, keep the data-fitted
# domain.
_ZERO_EXTEND_THRESHOLD = 0.25


@dataclass
class ColumnProfile:
    """Lightweight profile of a query result column for zero-baseline heuristics."""

    min_val: float | None = None
    max_val: float | None = None


def _is_temporal_value(value: Any) -> bool:
    """Return True if value looks like a date/time string or object."""
    if isinstance(
        value, (datetime, date)
    ):  # datetime is a subclass of date; both covered
        return True
    if not isinstance(value, str):
        return False
    date_patterns = [
        r"^\d{4}-\d{2}-\d{2}",
        r"^\d{2}/\d{2}/\d{4}",
        r"^\d{2}-\d{2}-\d{4}",
        r"^\d{4}/\d{2}/\d{2}",
        r"^\w{3}\s+\d{1,2},?\s+\d{4}",
        r"^\d{4}-Q[1-4]$",
        r"^Q[1-4]\s*\d{4}$",
        r"^\d{4}Q[1-4]$",
        r"^\d{4}-\d{2}$",
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$",
        r"^\d{4}-W\d{2}$",  # ISO 8601 week: 2024-W01
    ]
    return any(re.match(p, value, re.IGNORECASE) for p in date_patterns)


def _is_numeric_string(value: str) -> bool:
    """Return True if value is a string representation of a number."""
    try:
        float(value.replace(",", "").replace("$", "").replace("%", ""))
        return True
    except (ValueError, AttributeError):
        return False


def classify_column_type(
    column_name: str,
    sample_values: list[Any],
) -> str:
    """Classify a column as 'numeric', 'temporal', or 'categorical'."""
    name_lower = column_name.lower()
    temporal_patterns = [
        r"date",
        r"time",
        r"timestamp",
        r"created",
        r"updated",
        r"_at$",
        r"day",
        r"week",
        r"month",
        r"year",
        r"quarter",
        r"period",
    ]
    for pattern in temporal_patterns:
        if re.search(pattern, name_lower) and any(
            _is_temporal_value(v) for v in sample_values[:5]
        ):
            return "temporal"

    numeric_count = 0
    temporal_count = 0
    for val in sample_values:
        if isinstance(val, bool):
            # bool is a subclass of int — exclude it before the numeric branch
            # so booleans are not miscounted as numeric.
            pass
        elif isinstance(val, (int, float, Decimal)):
            numeric_count += 1
        elif isinstance(val, (datetime, date)):
            temporal_count += 1
        elif isinstance(val, str):
            if _is_temporal_value(val):
                temporal_count += 1
            elif _is_numeric_string(val):
                numeric_count += 1

    total = len(sample_values)
    if total == 0:
        return "categorical"
    if numeric_count / total > 0.8:
        return "numeric"
    if temporal_count / total > 0.8:
        return "temporal"
    return "categorical"


def is_column_discrete_for_bar_orientation(
    samples: list[int | float | Decimal | str | bool | date | datetime],
) -> bool:
    """True iff the column is categorical → bar x-axis should be horizontal.

    Strict: a Python ``str`` value is discrete even if it parses as a number.
    The database returned a string (VARCHAR/TEXT), so that is the declared type.
    Coercing ``"123"`` to numeric here would be a hidden data transformation.

    Rules:
    - ``int`` / ``float`` / ``Decimal`` (not bool) → continuous → False
    - ``datetime.date`` / ``datetime.datetime`` → continuous → False
    - Temporal-looking strings (``_is_temporal_value``) → continuous → False
    - Year-shaped strings (``is_year_shaped``) → continuous → False
    - Any other ``str`` or ``bool`` → discrete → True
    - Empty → False (no data to classify)

    Callers must pre-filter None values; None is not a valid sample.
    """
    if not samples:
        return False
    if is_year_shaped(samples):
        return False
    for v in samples:
        if isinstance(v, bool):
            return True  # bool is not a numeric axis
        if isinstance(v, (int, float, Decimal, date, datetime)):
            continue  # native numeric/temporal → continuous
        if _is_temporal_value(v):
            continue  # temporal-looking string → continuous
        return True  # non-temporal string → discrete
    return False


def first_non_null_samples(
    field: str, data: list[dict[str, Any]], limit: int = 20
) -> list[Any]:
    """Return up to `limit` non-null values of `field`, scanning rows in order.

    Slicing `data[:limit]` before filtering nulls starves the sample when the
    first rows happen to be null — this walks rows until `limit` values are
    collected (or the data is exhausted).
    """
    samples: list[Any] = []
    for row in data:
        value = row.get(field)
        if value is not None:
            samples.append(value)
            if len(samples) == limit:
                break
    return samples


def _pick_scale(
    profile: ColumnProfile, chart_type: str | None = None
) -> dict[str, Any] | None:
    """Decide the explicit zero setting for the y-axis scale.

    Returns ``{"zero": False}`` to keep the domain data-fitted, ``{"zero": True}``
    to extend to zero, or ``None`` when zero is not relevant (data spans or
    touches zero, or no data profiled).

    Optional-zero chart types (line, scatter): smart-auto heuristic. Extend
    to zero when data_min/data_max <= 0.25 (data starts in the bottom quarter
    of [0, max]) — returns ``None`` there, not an explicit ``True``. ``None``
    only means the explicit ``scale.continuous.zero`` override stays unset;
    it is not a no-op. Each family separately re-derives the same
    close-to-zero-so-anchor verdict for its own tick ladder (the
    ``zero_anchor`` argument to ``_resolve_cartesian_ticks``); on its own that
    argument only feeds tick placement and headroom-expanded ``domainMax``,
    never a ``domainMin`` floor. Line's multi-metric path is the exception:
    it feeds the same re-derived verdict into ``_bake_zero_flag``, which
    bakes ``scale.continuous.zero`` explicitly and does produce a
    ``domainMin`` floor at emit. The visible baseline itself comes from
    ``BaselineFeature``'s render-time ``datum: 0`` rule.

    Non-optional-zero types (bar, area) with all-positive data: always
    extend to zero — both encode absolute magnitude (a bar's length, an
    area's fill), and truncating either misstates the quantity. Returns an
    explicit True so resolved_chart.zero carries the intent and the render
    layer needs no chart-type knowledge to decide domain pinning.

    All-negative data (max < 0): mirrored, not skipped. A magnitude encoding
    truncated by a floor at -104 misstates the quantity exactly as one
    truncated by a floor at +76 does, and the ratio branch reads the same way
    against the near edge. The whole point is that the domain and the
    render-time ``datum: 0`` rule must agree: a chart that draws the baseline
    needs 0 in its domain, and a chart that bakes ``zero: False`` draws no
    baseline and keeps its fitted domain.
    """
    if profile.min_val is None or profile.max_val is None:
        return None
    if profile.max_val == 0:
        return None
    # Spanning zero: it is already in the domain, so nothing needs pinning.
    if profile.min_val < 0 < profile.max_val:
        return None

    # Which edge is nearer zero, and which one the domain would have to reach
    # past to include it. All-positive: near=min, far=max. All-negative:
    # mirrored.
    all_negative = profile.max_val < 0
    near, far = (
        (profile.max_val, profile.min_val)
        if all_negative
        else (profile.min_val, profile.max_val)
    )

    if chart_type in _OPTIONAL_ZERO_CHART_TYPES:
        # Smart-auto: keep data-fitted when data lives too far from zero.
        ratio = near / far
        if ratio > _ZERO_EXTEND_THRESHOLD:
            return {"zero": False}
        return None

    # Data that TOUCHES zero needs no opinion either way: the domain already
    # includes 0, so there is nothing to extend, and saying so would pin a
    # `zero: True` that suppresses Vega-Lite's nice-rounding on a ladderless
    # theme. The mirrored guard for the negative side is the `max_val == 0`
    # return above.
    if near == 0:
        return None

    # Non-optional-zero types (bar, area): return explicit True so
    # resolved_chart.zero carries the intent and the render layer needs no
    # chart-type knowledge to decide domain pinning.
    return {"zero": True}
