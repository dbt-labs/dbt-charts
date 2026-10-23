"""Shared helpers for Vega-Lite type inference from query result data."""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING, Any, TypeAlias

from dbt_charts.core.compile.resolve.chart.tick_values import (
    zero_anchor_domain_floor,
    zero_anchor_floor,
)
from dbt_charts.core.constants.axis import MAX_ORDINAL_BUCKETS
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.utils import CellValue, vega_infers_quantitative

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedAxisStyle,
        ResolvedScaleStyle,
    )

# Date-only ISO forms (YYYY-MM, YYYY-MM-DD) — the only string shapes JS
# Date.parse treats as UTC midnight, making them safe domain values for the
# labels.values epoch-ms membership filter on ordinal axes. Datetime strings
# without an offset parse as LOCAL time in JS and are deliberately excluded.
_ISO_UTC_SAFE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?$")

DetectedTimeUnit: TypeAlias = str | None


DATE_LIKE_PATTERNS = [
    r"^\d{4}-Q[1-4]$",
    r"^Q[1-4]\s*\d{4}$",
    r"^\d{4}Q[1-4]$",
    r"^\d{4}-\d{2}$",
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$",
    r"^FY\d{4}$",
    r"^H[12]\s*\d{4}$",
    r"^\d{4}-H[12]$",
    r"^W(?:eek\s*)?\d{1,2}\s*\d{4}$",
    r"^\d{4}-W\d{2}$",  # ISO 8601 week: 2024-W01
    r"^\d{2}/\d{4}$",  # MM/YYYY: 01/2024
    r"^\d{2}/\d{2}/\d{4}$",  # MM/DD/YYYY: 01/15/2024
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$",  # Mon DD, YYYY
    r"^\d{4}-\d{2}-\d{2}$",  # YYYY-MM-DD: 2024-01-15
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$",  # ISO 8601 naive timestamp, T or space separator: 2024-01-15T13:30:00 / 2024-01-15 13:30:00
]
DATE_LIKE_REGEXES = [
    re.compile(pattern, re.IGNORECASE) for pattern in DATE_LIKE_PATTERNS
]


def is_date_like_string(value: str) -> bool:
    """Return True when a string looks like an ordered date bucket."""
    return any(pattern.match(value) for pattern in DATE_LIKE_REGEXES)


# Year-leading patterns whose lexicographic ascending order equals chronological
# order. Safe to use as a window-sort field for x-cell sampling.
# Patterns NOT listed here (e.g. "Jan 2024", "01/2024", "Q1 2024") sort
# lex-incorrectly and must NOT trigger sampling — wrong cells would be selected.
# Every entry must also appear in DATE_LIKE_PATTERNS (the module-level assert
# below enforces this so the two lists cannot silently drift).
_LEX_SORTABLE_DATE_LIKE_PATTERNS = [
    r"^\d{4}-Q[1-4]$",  # 2024-Q1 lex == chron
    r"^\d{4}Q[1-4]$",  # 2024Q1 lex == chron
    r"^\d{4}-\d{2}$",  # 2024-01 lex == chron
    r"^FY\d{4}$",  # FY2024 lex == chron
    r"^\d{4}-H[12]$",  # 2024-H1 lex == chron
    r"^\d{4}-W\d{2}$",  # 2024-W01 lex == chron (ISO 8601 week)
    r"^\d{4}-\d{2}-\d{2}$",  # 2024-01-15 lex == chron
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$",  # naive ISO timestamp lex == chron (T or space, no tz suffix)
]
# Guard against drift: every lex-sortable pattern must be a subset of the
# full date-like list so a future edit to DATE_LIKE_PATTERNS can't leave
# is_lex_sortable_date_like silently matching patterns that were tightened.
# Use RuntimeError (not assert) so the guard survives python -O.
if not set(_LEX_SORTABLE_DATE_LIKE_PATTERNS) <= set(DATE_LIKE_PATTERNS):
    raise RuntimeError(
        "_LEX_SORTABLE_DATE_LIKE_PATTERNS contains patterns not in DATE_LIKE_PATTERNS; "
        "update or remove the stale entries"
    )


# Bar-family marks normally hide x ticks because every bucket is labeled. Once
# calendar labels become sparser than buckets, ticks restore the missing
# positional cue without changing which bucket positions the axis contains.


def resolve_authored_x_type(axis: ResolvedAxisStyle) -> str | None:
    """Authored ``axis_x.type``, with ``axis_x.scale.type: temporal`` as a
    second escape hatch for the same authored-temporal request — authors
    reach for ``scale.type``, not ``type``, since that's the field VL itself
    calls "scale type". The explicit ``axis.type`` wins when both are
    authored so there is one unambiguous override, not two competing ones.
    Only ``"temporal"`` participates; the quantitative Literal values
    (log/pow/sqrt/symlog) are meaningless on a dimension axis.
    """
    authored_type = axis.type
    if authored_type in (None, "auto") and axis.scale is not None:
        _ax_scale_type = (
            axis.scale.continuous.type if axis.scale.continuous is not None else None
        )
        if _ax_scale_type == "temporal":
            authored_type = "temporal"
    return authored_type


def _paneled_x_values(
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query rows
    x_field: str,
    panel_fields: tuple[str, ...],
) -> list[list[CellValue]]:
    """Group *data*'s x cells by panel key — one inner list per panel.

    With no ``panel_fields`` this is the one-panel case and returns a single
    group — exactly the pooled list the non-faceted path has always measured.

    A declared panel field is always present on every row: ``all_rows()``
    re-stamps the partition columns unconditionally, and ``regroup`` raises
    ``ERR-MULTIPLES-ROW-MISSING-PARTITION-FIELD`` before this runs otherwise.
    So there is no "field missing" branch to fall back on — silently pooling
    there would be the exact measurement this grouping exists to prevent.
    """
    if not panel_fields:
        return [[row.get(x_field) for row in data if x_field in row]]
    groups: dict[tuple[CellValue, ...], list[CellValue]] = {}
    for row in data:
        if x_field not in row:
            continue
        key = tuple(row.get(f) for f in panel_fields)
        groups.setdefault(key, []).append(row[x_field])
    return list(groups.values())


def resolve_cartesian_x_type(
    data: ChartRenderData,
    x_field: str,
    axis: ResolvedAxisStyle,
    mark_type: str,
    is_band_step: bool,
    panel_fields: tuple[str, ...],
) -> tuple[str, str, DetectedTimeUnit]:
    """Return the emitted VL x type, data type, and calendar bucket grain.

    Channel-agnostic: a heatmap calls it for its y axis too.

    Any auto-detected bucketed grain is additionally gated by
    ``ordinal_scaffold_within_budget``: past the budget the data is sparser
    than its detected grain, so the bar branch resolves temporal instead of
    ordinal, and the grain itself is dropped (returned time_unit None) so no
    caller bands mark widths or label cadences to a bucket the data doesn't
    actually have. Grain drives more than mark banding — it also paints one
    gridline and label per bucket — so line/area/scatter consult the same
    gate bar does, not just the families that band mark widths. (heatmap
    returns above the gate.) A coarse grain is no exemption: a decade-spaced
    series names ``year`` honestly and still owes ten empty bands per bar,
    which the coarse branch's row count cannot see — it counts only the bars
    that exist. An authored ``time_unit`` is an instruction, not a guess —
    never gated.

    ``panel_fields`` names the chart's partition (small-multiples) columns.
    The scaffold is built per panel, so the budget must be measured per
    panel. This stays a pure function of its arguments — same rows, same
    fields, same verdict — which is what lets the gate-reaching call sites
    agree. It does NOT mean every site sees one verdict chart-wide:
    ``_channels.py`` resolves on PRE-gap-fill rows to route gap-fill, while
    ``bar.py`` and the warning detector resolve on the POST-fill rows, and an
    authored ``fill`` makes those inputs differ by construction (see the
    re-sort note in ``_channels.py``). Omitting it is the non-faceted case;
    It is REQUIRED on ``resolve_cartesian_x_type`` — the function that owns
    the gate — so a new caller cannot take the pooled-span verdict by
    omission. the engine's x-axis builder keeps a ``()`` default (it is the
    wrapper every emitter test constructs directly); its production callers
    all pass explicitly, and it forwards whatever it is given.
    """
    from dbt_charts.core.render.chart.time_unit_detect import (
        BUCKETED_CALENDAR_UNITS,
        FINE_BUCKET_UNITS,
        TIME_PART_UNITS,
        detect_time_unit,
        ordinal_scaffold_within_budget,
    )

    x_type_from_data = infer_vega_type_from_data(data, x_field)
    authored_time_unit = axis.time_unit
    authored_type = resolve_authored_x_type(axis)

    if authored_time_unit is None and x_type_from_data == "temporal":
        time_unit = detect_time_unit(
            [row.get(x_field) for row in data if x_field in row]
        )
    elif authored_time_unit is None and x_type_from_data == "ordinal":
        try:
            time_unit = detect_time_unit(
                [row.get(x_field) for row in data if x_field in row]
            )
        except ValueError:
            time_unit = None
    else:
        time_unit = authored_time_unit

    if mark_type == "heatmap":
        # Grid dimension: always a nominal band scale — promoting it collapses
        # every rect cell to zero width (matches oracle _map_rect). time_unit
        # is still returned so the engine's x-axis builder gets the same
        # labelExpr/tick-thinning enrichment the ordinal branch gives bar.
        return "nominal", x_type_from_data, time_unit

    scaffold_ok = (
        ordinal_scaffold_within_budget(
            _paneled_x_values(data, x_field, panel_fields), time_unit
        )
        if authored_time_unit is None and time_unit in BUCKETED_CALENDAR_UNITS
        else True
    )

    if (
        authored_type == "temporal"
        or authored_time_unit == "none"
        or (time_unit is not None and time_unit in TIME_PART_UNITS)
    ):
        vl_type = "temporal"
    elif authored_type == "ordinal":
        vl_type = "ordinal"
    elif time_unit and time_unit in BUCKETED_CALENDAR_UNITS:
        if is_band_step:
            vl_type = "ordinal"
        elif mark_type in ("line", "area", "scatter"):
            vl_type = "temporal"
        elif time_unit in FINE_BUCKET_UNITS:
            vl_type = "ordinal" if scaffold_ok else "temporal"
        else:
            n_buckets = len({row.get(x_field) for row in data if x_field in row})
            vl_type = (
                "ordinal"
                if scaffold_ok and n_buckets <= MAX_ORDINAL_BUCKETS
                else "temporal"
            )
    else:
        vl_type = x_type_from_data

    # A continuous scale carrying an over-budget grain must not keep the
    # grain either: the bar emitter bands mark widths to the timeUnit (a
    # "daily" bar is one sub-pixel day wide on a multi-year span) and the
    # label ladder paints one tick per bucket — line/area/scatter don't band
    # mark widths, but the same gridline/label-per-bucket cost applies to
    # them too. Dropping it hands the axis to VL's own continuous-temporal
    # defaults for every family.
    if vl_type == "temporal" and not scaffold_ok:
        time_unit = None

    if (
        vl_type == "temporal"
        and time_unit in {"year", "yearquarter"}
        and axis.fiscal_year_start_month != 1
    ):
        vl_type = "ordinal"
    return vl_type, x_type_from_data, time_unit


def zero_anchor_pinned_floor(axis: ResolvedAxisStyle) -> float | None:
    """The ``domainMin`` a zero-anchored measure scale pins, or None when no
    rung supplies one.

    The single home of that decision. Four places build a zero-anchored
    measure scale — this module's ``y_zero_scale`` (line/area/scatter, and
    bar's stacked-vertical branch) plus bar's own unstacked-vertical and
    horizontal branches — and each has to answer it the same way:

    - The ladder is filtered through ``zero_anchor_domain_floor`` first. An
      authored ``scale.values`` lands verbatim in ``ay.tick_values`` and is a
      statement about tick positions, not the domain, so it must never source
      a floor. Taking the axis rather than a pre-filtered list is the point:
      passing the ladder in is what let callers forget the filter. ``scale``
      and ``tick_values`` are both declared unconditionally, so neither needs
      a defensive read.
    - No rung means nothing pins the edge. Returning ``None`` rather than a
      literal ``0.0`` matters: 0.0 is a floor only while the data is
      non-negative, and on all-negative data it pins 0 as the BOTTOM of the
      domain, collapsing every mark onto one pixel row.

    Callers compose the result differently and legitimately so — one applies
    resolve's headroom bake afterwards, another only pins when that bake is
    absent, a third pairs the no-rung case with ``nice: False``. Only the
    decision is shared.
    """
    rungs = zero_anchor_domain_floor(
        axis.scale.values if axis.scale is not None else None,
        list(axis.tick_values),
    )
    return zero_anchor_floor(rungs) if rungs else None


def is_zero_anchored(scale: ResolvedScaleStyle | None) -> bool:
    """True when this measure scale anchors its domain at zero.

    The one spelling of that question: ``y_zero_scale`` branches on it to pin a
    ``domainMin``, and anything that needs to know where the axis actually
    starts reads the same answer rather than re-deriving it. Takes the scale
    rather than the axis so neither caller needs a duck-typed attribute read.
    """
    cont = scale.continuous if scale is not None else None
    return (cont.zero if cont is not None else None) is True


def infer_vega_type_from_data(data: list[dict[str, Any]], field: str) -> str:
    """Infer the most appropriate Vega-Lite type for a field."""
    if not data or field not in data[0]:
        return "nominal"

    sample_values = [
        row.get(field) for row in data[: min(10, len(data))] if field in row
    ]
    if not sample_values:
        return "nominal"

    # vega_infers_quantitative (core/utils.py) re-derives this exact verdict
    # from the same first-10-rows sample -- the shared leaf predicate
    # compile/resolve/chart/_channels.py's compile-time magnitude gate calls
    # too, so the two can no longer independently drift on what counts as
    # numeric. Checked first and returned early: the temporal/date-like scan
    # below is wasted work once a column is already quantitative, since a
    # column can never be both.
    if vega_infers_quantitative(data, field):
        return "quantitative"

    all_temporal = True
    all_date_like = True

    for value in sample_values:
        if value is None:
            continue
        if isinstance(value, (dt.date, dt.datetime)):
            # date/datetime objects are always temporal; date_like too
            pass
        elif isinstance(value, str):
            # Require an actual date-like structure: YYYY-MM-DD, YYYY/MM/DD,
            # MM/DD/YYYY, or ISO timestamps. The old check (`count("-") >= 2`)
            # was too broad and misidentified strings like "claude-opus-4-7"
            # (3 hyphens, no digits in date positions) as temporal.
            is_standard_date = bool(
                re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}([T ][\d:.].*)?$", value)
                or re.match(r"^\d{2}/\d{2}/\d{4}$", value)
            )
            if not is_standard_date:
                all_temporal = False
            if not is_standard_date and not is_date_like_string(value):
                all_date_like = False
        else:
            all_temporal = False
            all_date_like = False

    if all_temporal:
        return "temporal"
    if all_date_like:
        return "ordinal"
    return "nominal"
