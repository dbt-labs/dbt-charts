"""dbt charts-shaped helpers for axis tick computation — compile boundary.

The generic "nice step" tick algorithm lives in the neutral
``dbt_charts.core.numeric`` leaf (both compile and render need it); this module
holds the dbt charts-typed helpers built on top of it (domain parsing, stacked-
bar totals) that stay compile-owned.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.resolve.chart._chart_rows import PanelRows

ChartValue = str | int | float | Decimal
# stacked_bar_totals's row shape -- a named alias so the type-state gate
# counts its `Any` once. Its PanelRows-typed callers (stacked_bar_totals_max,
# stacked_bar_multi_measure_totals_max) don't use this: PanelRows already
# satisfies this wider shape structurally, no separate alias needed for them.
_RowData = list[dict[str, Any]]


def apply_headroom(
    value: float, headroom: float | None, cap: float | None = None
) -> float:
    """Multiply a positive domain top by ``1 + headroom``, exact (no rounding).

    ``headroom`` of ``None`` or ``0`` is a no-op. Negative headroom is rejected
    by the ``ScaleStyle.headroom`` ``ge=0`` constraint, never reaches here.

    Only applies to a positive ``value`` — a non-positive measure-axis max (all
    data at or below zero) has no well-defined multiplicative "top", so it
    passes through unchanged rather than growing more negative.

    ``cap`` is a ceiling the headroom may not cross (a percent axis stops at
    1.0); it never lowers ``value`` itself.
    """
    if not headroom or value <= 0:
        return value
    top = value * (1.0 + headroom)
    return top if cap is None else max(value, min(top, cap))


def numeric_domain_bounds(domain: object) -> tuple[float, float] | None:
    """Return ``(lo, hi)`` when ``domain`` is a 2-element list/tuple of reals.

    An authored quantitative ``scale.domain`` feeds these bounds into
    ``nice_tick_values`` so the tick ladder spans the authored range, not just
    the data extent. Returns ``None`` for an unset, non-numeric (categorical /
    ordinal), or malformed domain — the caller then falls back to the data
    extent unchanged.
    """
    if not isinstance(domain, (list, tuple)) or len(domain) != 2:
        return None
    lo, hi = domain
    if (
        isinstance(lo, (int, float))
        and not isinstance(lo, bool)
        and isinstance(hi, (int, float))
        and not isinstance(hi, bool)
    ):
        return float(lo), float(hi)
    return None


def zero_anchor_domain_floor(
    scale_values: list[Any] | None,  # type-state: explicit_any — mirrors scale.values
    tick_values: list[float],
) -> list[float]:
    """*tick_values*, or ``[]`` when *scale_values* (the axis's authored
    ``scale.values``) is set.

    A computed zero-anchored ladder is a matched set whose bottom rung is
    always <= the data floor, so pinning a domain floor from it can never
    exclude data. An authored ``scale.values`` list carries no such
    guarantee -- it's a statement about tick positions, not the domain -- so
    an authored list must never source a floor pin. The single home of this
    provenance distinction, shared by both sides of the ``compile
    <-> render`` boundary: render's ``resolve_measure_y_scale``/
    ``y_zero_scale`` (``render/chart/emitters/_cartesian.py``,
    ``render/chart/type_inference.py``) and horizontal bar's own
    measure-axis scale (``render/chart/emitters/bar.py``) filter through it
    before pinning a VL ``domainMin``; compile's ``_y_domain_floor``
    (``compile/resolve/chart/_domain.py``) filters through the SAME call to
    ask its own question at resolve time — which value renders at the plot's
    bottom edge, deciding whether an x-axis tick stub is needed. Same filter,
    different questions: see ``zero_anchor_floor`` below on where the two
    answers now diverge. A caller re-deriving ``scale.values is not None`` inline
    instead of calling this is the bug this function exists to prevent.
    """
    if scale_values is not None:
        return []
    return tick_values


def zero_anchor_floor(tick_values: list[float] | None) -> float:
    """The exact ``domainMin`` a zero-anchored axis pins, given *tick_values*
    already filtered through ``zero_anchor_domain_floor`` (``None``/empty are
    the same "no usable rung" state -- a caller with no ladder at all passes
    ``None`` rather than manufacturing an empty list first).

    Its lowest rung, or the literal ``0.0`` fallback when the ladder was
    filtered out (an authored ``scale.values``) or never baked.

    That ``0.0`` is NOT what render emits any more. Render goes through
    ``zero_anchor_pinned_floor``, which returns None for the no-rung case
    instead: 0.0 is a legal floor only while the data is non-negative, and on
    an all-negative axis it pins 0 as the BOTTOM of the domain. This
    function keeps the literal for compile's ``_y_domain_floor``, which asks
    a different question -- which value renders at the plot's bottom edge,
    where ``zero: true`` on non-negative data really does land on 0.
    """
    return tick_values[0] if tick_values else 0.0


def stacked_bar_totals(
    data: _RowData,
    x_field: str,
    y_field: str,
) -> dict[ChartValue, float]:
    """Return each category's positive stacked total, keyed by its ``x_field`` value.

    Sums only positive y values per category, matching VL's behavior of stacking
    positives upward and negatives downward independently.

    ``data`` stays the wide ``_RowData`` type, not ``PanelRows``: render's
    ``bar_hover_band.py`` calls this directly against its own flat rows, with
    no panel concept of its own. ``stacked_bar_totals_max`` below is the one
    ``PanelRows``-typed caller — a ``PanelRows`` argument satisfies this wider
    parameter type for free (``PanelRows`` is a ``NewType`` over this same
    ``list[dict[str, Any]]`` shape).
    """
    totals: dict[ChartValue, float] = {}
    for row in data:
        x = row.get(x_field)
        y = row.get(y_field)
        if x is None or y is None:
            continue
        totals[x] = totals.get(x, 0.0) + max(0.0, float(y))
    return totals


def stacked_bar_totals_max(
    data: PanelRows,
    x_field: str,
    y_field: str,
) -> float | None:
    """Return the max per-category positive stacked total, or None if no valid rows.

    Caller must guard against stack_mode "normalize" (domain is always [0, 1] there).

    ``data`` is one panel's rows (``PanelRows``), never the whole dataset —
    summing across every small-multiples panel inflates the shared-scale
    domain by the panel count. Fold across panels via
    ``fold_panels``/``reduce_panels``, never by widening this signature.
    """
    totals = stacked_bar_totals(data, x_field, y_field)
    return max(totals.values()) if totals else None


def stacked_bar_multi_measure_totals_max(
    data: PanelRows,
    x_field: str,
    y_fields: list[str],
) -> tuple[float, ...]:
    """Return the max positive stacked total for wide-form measures.

    ``data`` is one panel's rows — see ``stacked_bar_totals_max``.
    """
    totals: defaultdict[str | int | float | Decimal, float] = defaultdict(float)
    for row in data:
        x = row.get(x_field)
        if x is None:
            continue
        totals[x] += sum(
            max(0.0, float(row[y])) for y in y_fields if row.get(y) is not None
        )
    return (max(totals.values()),) if totals else ()


def stacked_totals_max(
    data: PanelRows, x_field: str, y_fields: list[str]
) -> float | None:
    """One-panel stacked total max, single- or multi-measure — for ``fold_panels``.

    Thin dispatcher over ``stacked_bar_totals_max`` /
    ``stacked_bar_multi_measure_totals_max`` so a caller folding across panels
    (``fold_panels(dataset, scale, lambda rows: stacked_totals_max(rows, x, y_fields))``)
    doesn't need its own single-vs-multi branch.
    """
    if len(y_fields) == 1:
        return stacked_bar_totals_max(data, x_field, y_fields[0])
    totals = stacked_bar_multi_measure_totals_max(data, x_field, y_fields)
    return totals[0] if totals else None
