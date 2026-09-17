"""Shared helpers for Vega-Lite type inference from query result data."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.resolve.chart.tick_values import (
    zero_anchor_domain_floor,
    zero_anchor_floor,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.vl_field_maps import emit_resolved_scale_vl
from dbt_charts.core.text.format_d3 import is_time_format
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


def _utc_time_label_expr(fmt: str) -> str:
    """Build a Vega ``labelExpr`` that formats a date-string axis tick under UTC.

    ``toDate(datum.value)`` parses ISO date strings (ordinal domain) or accepts
    Date instances unchanged; ``utcFormat`` emits the requested d3-time-format
    string in UTC so the spec renders identically under any runtime TZ.
    """
    fmt_escaped = fmt.replace("\\", "\\\\").replace("'", "\\'")
    return f"utcFormat(toDate(datum.value), '{fmt_escaped}')"


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
_LEX_SORTABLE_DATE_LIKE_REGEXES = [
    re.compile(p, re.IGNORECASE) for p in _LEX_SORTABLE_DATE_LIKE_PATTERNS
]

_TEMPORAL_TICK_INTERVAL: dict[str, tuple[str, int]] = {
    "year": ("year", 1),
    "yearquarter": ("month", 3),
    "yearmonth": ("month", 1),
    "yearweek": ("week", 1),
    "yearmonthdate": ("day", 1),
}

# Bar-family marks normally hide x ticks because every bucket is labeled. Once
# calendar labels become sparser than buckets, ticks restore the missing
# positional cue without changing which bucket positions the axis contains.
_LABEL_THINNING_TICK_MARK_TYPES = frozenset({"bar", "histogram"})


def is_lex_sortable_date_like(value: str) -> bool:
    """Return True when the string is a date-like bucket whose lex sort is chronological.

    Only year-leading ISO-prefix patterns qualify. Month-name, MM/YYYY, and
    quarter-first patterns sort incorrectly under string comparison and must
    not be used to drive the sampling window's ascending sort.
    """
    return any(p.match(value) for p in _LEX_SORTABLE_DATE_LIKE_REGEXES)


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
    omission. ``build_cartesian_x_encoding`` keeps a ``()`` default (it is the
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
        # is still returned so build_cartesian_x_encoding gets the same
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
            max_ordinal = get_chart_rendering().type_inference.max_ordinal_buckets
            vl_type = (
                "ordinal" if scaffold_ok and n_buckets <= max_ordinal else "temporal"
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


def temporal_edge_labels_flushed(vl_type: str, axis: ResolvedAxisStyle) -> bool:
    """Whether Vega flushes this temporal axis's outer labels into the plot."""
    return vl_type == "temporal" and axis.labels.flush is not False


def apply_x_tick_cadence(
    axis_vl: VLDict | None,
    axis: ResolvedAxisStyle,
    x_field: str,
    vl_type: str,
    *,
    remedy: str | None = None,
) -> None:
    """Merge authored ``count``/``step`` onto an x-axis VL dict, in place.

    The one home for x-axis tick cadence, gate and merge together. Every
    cartesian x path calls this exactly once — through
    ``build_cartesian_x_encoding``, or directly where an emitter builds its
    own axis (scatter's quantitative fast path, histogram, the multi-measure
    heatmap, horizontal bar). A path that skips it turns an authored cadence
    back into a silent no-op, which is the defect this surface exists to
    remove.

    ``remedy`` overrides the "what to do instead" half of the error for a
    caller whose axis needs a better answer than the type alone can give.
    ``_emit_horizontal`` passes one: its ``axis_x`` is the CATEGORICAL axis
    (the measure is ``axis_y``), so "this domain is discrete" would leave an
    author who wrote ``axis_x`` meaning "the horizontal axis" no wiser — it
    names the orientation instead, exactly as it already does for
    ``labels.values``.

    The gate and the merge are one function on purpose. They were two, and the
    obligation to pair them correctly got half-done twice: once dropping the
    merge guard (a theme's tick density silently thinning an authored
    ``scale.values`` ladder), once dropping the call entirely.

    ``axis_vl`` is None on a path that draws no axis dict at all — the
    multi-measure heatmap's layered top-level x. Nothing can be merged there,
    but the band still renders category labels, so the gate must still fire.

    What merges, and what does not:

    - ``count`` is a target, not a ladder. VL honors it closely on a temporal
      scale and rounds to a nearby nice step on a quantitative one. A discrete
      scale has no tick-count concept, so it falls through unset — that has
      always been a silent no-op and stays one.
    - A bare ``step`` (no ``time_unit``) is the numeric interval lever, so off
      a quantitative scale it raises rather than no-ops: an explicit cadence is
      an explicit request. The remedy varies with what the author can actually
      do instead — on temporal, ``step`` needs a unit to name a calendar
      cadence; on a discrete domain nothing applies, and pointing at
      ``ticks.count`` there would just name a second inert field.
    - An existing ``values`` suppresses the whole merge (an enumerated ladder
      names every tick outright), and ``setdefault`` never overwrites a
      ``tickCount`` the caller already resolved. The gate fires either way: a
      misauthored cadence is an error whether or not anything would have read
      it.

    The temporal ``{interval, step}`` construction stays in
    ``build_cartesian_x_encoding``, which needs the resolved label grain. So
    does the ``ticks.time_unit`` gate, for a different reason: it is unchanged
    from before this surface existed, and the paths that skip
    ``build_cartesian_x_encoding`` have always silently ignored ``time_unit``.
    Moving it would newly break boards that render today.
    """
    ticks = axis.ticks
    out: dict[str, int] = {}

    if ticks.step is not None and ticks.time_unit is None:
        if vl_type != "quantitative":
            from dbt_charts.core.diagnostics.chart_data import ChartDataError
            from dbt_charts.core.diagnostics.codes_render import (
                ERR_TICKS_STEP_NOT_QUANTITATIVE,
            )

            raise ChartDataError.from_code(
                ERR_TICKS_STEP_NOT_QUANTITATIVE,
                field=x_field,
                vl_type=vl_type,
                remedy=remedy
                or (
                    "Author ticks.time_unit alongside step (e.g. time_unit: "
                    "year, step: 5) to name a calendar cadence."
                    if vl_type == "temporal"
                    else "This axis has a discrete domain, which has no "
                    "numeric tick interval — remove ticks.step."
                ),
            )
        out["tickMinStep"] = ticks.step

    if ticks.count is not None and vl_type in ("temporal", "quantitative"):
        out["tickCount"] = ticks.count

    if axis_vl is None or "values" in axis_vl:
        return
    for key, value in out.items():
        axis_vl.setdefault(key, value)


HEATMAP_FORMAT_REMEDY = (
    "Remove the format — a heatmap has no measure axis. Both axes are grid "
    "dimensions and the value lives on the color channel, which carries no "
    "label format of its own."
)


# Spellings Python's float() accepts and JS's unary + does not, so the two
# disagree on whether a tick paints as a number: digit separators, and the
# non-finite words (JS reads "nan"/"inf" as NaN and paints exactly that).
_NOT_JS_NUMERIC_RE = re.compile(r"_|^[+-]?(nan|inf(inity)?)$", re.IGNORECASE)


def _reads_as_number(value: Any) -> bool:  # type-state: explicit_any — a raw query cell
    """Whether d3 can read this tick value as a number, i.e. JS ``+value``.

    Neither of the two numeric predicates this repo already has answers this
    question, which is why it is a third one:

    - ``coerce_numeric_cell`` is the shared *null* rule ("no color, no domain
      contribution") and excludes ``bool``, a contract this question does not
      share — d3 reads ``+true`` as ``1`` and paints ``0``/``1`` over a boolean
      dimension rather than NaN.
    - ``is_vega_numeric_value`` (``core/utils.py``) is the "what VL type is
      this column" rule and deliberately rejects numeric *strings*. d3
      coerces those, so rejecting them here would refuse a column that
      formats perfectly.

    Do not consolidate this into either of them: each rejection above is a
    board that renders today.
    """
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True  # JS reads +"" and +" " as 0
        if _NOT_JS_NUMERIC_RE.search(text):
            return False
        try:
            float(text)
        except ValueError:
            return False
        return True
    return False


def gate_label_format(
    fmt: str | None,
    field: str,
    data: list[dict[str, Any]],  # type-state: explicit_any — raw query rows
    vl_type: str,
    *,
    setting: Literal[
        "axis_x.labels.format",
        "axis_y.labels.format",
        "axis_y.mirror.format",
    ],
    remedy: str | None = None,
) -> None:
    """Raise when a d3 number format is authored over ticks it cannot paint.

    The one gate for every axis-format surface: ``axis_x``, ``axis_y``, the
    mirror ghost, and heatmap's y all call this rather than each inventing
    its own drop/NaN/literal-text behavior. Two failures share the code:

    - **A band scale** (``vl_type`` nominal or ordinal) whose **ticks d3
      cannot read as numbers**, per ``_reads_as_number`` — JS ``+value``,
      the only rule that decides whether a tick paints as a number or as
      ``NaN``. Numeric categories (``stage_id: 1, 2, 3``), numeric strings
      and booleans all format cleanly, so they stay legal: a misaddressed
      format is indistinguishable from a wanted one there. Date-like buckets
      (``2024-01``, ``Q1 2024``) do NOT coerce and are the case worth
      naming: "revenue by quarter as a horizontal bar" is routine, and it
      NaNs like any other category.
    - **A temporal scale** with a non-time spec, unconditionally — Vega reads
      the spec as a time spec and paints its literal text (e.g. repeated
      ``$,.0f``) across the axis instead of the date. Unlike the band case
      there is no numeric-tick exemption: no reading of a number format over
      dates was ever the author's intent, so this half skips the row walk
      entirely.

    A quantitative axis, or any axis with no authored format, is a no-op —
    the axis paints exactly what was authored, format included.

    ``setting`` is the authored keypath the caller is gating, verbatim, and
    the error message opens with it. It is the whole path rather than just
    the channel because a mirror ghost gates ``axis_y.mirror.format`` — a
    different key from ``axis_y.labels.format``, and naming the latter would
    send the author to a setting they never wrote.

    ``remedy`` overrides the "what to do instead" half for a caller whose
    axis needs a better answer than the field type alone can give — the same
    shape ``apply_x_tick_cadence`` already takes. A caller whose axis has no
    "move it to the other channel" fix (heatmap, the mirror ghost, a scatter
    dot plot whose measure sibling is ``axis_x``) MUST pass one: the band
    default names ``axis_y``, and following that advice anywhere else sends
    the author straight back into the failure this raises for. Temporal is
    the exception that needs NO caller remedy — the gate answers it with the
    ``style.time_format`` text below, which is right on every family, so a
    temporal call site passing ``remedy=None`` is correct rather than lazy.
    """
    if fmt is None or is_time_format(fmt):
        return
    if vl_type not in ("nominal", "ordinal", "temporal"):
        return
    # EVERY row, not a sample: Vega paints a tick per domain value, so ten
    # numeric rows followed by one "AAA" is exactly the NaN this exists to
    # catch. `all` short-circuits on the first non-numeric cell, and an
    # all-numeric column is one cheap pass over rows the renderer already
    # walks several times. An empty generator — no rows, or the field
    # absent from all of them — is missing evidence, not a category axis,
    # and `all` answers True for it.
    if vl_type != "temporal" and all(
        _reads_as_number(value)
        for value in (row.get(field) for row in data)
        if value is not None
    ):
        return
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_LABEL_FORMAT_AXIS_MISMATCH,
    )

    default_remedy = (
        "This axis is temporal — d3's number grammar doesn't read over "
        'dates. Author a time spec here (e.g. "%b %Y"), or set '
        "style.time_format instead."
        if vl_type == "temporal"
        else (
            "axis_x addresses the dimension channel — author the number "
            "format on style.axis_y.labels.format, the measure axis."
        )
    )
    raise ChartDataError.from_code(
        ERR_LABEL_FORMAT_AXIS_MISMATCH,
        setting=setting,
        field=field,
        fmt=fmt,
        remedy=remedy
        or default_remedy,  # type-state: silent_fallback — None means the default text
    )


def build_cartesian_x_encoding(
    data: list[dict[str, Any]],
    x_field: str,
    axis: ResolvedAxisStyle | Any,
    ax_vl: dict[str, Any],
    mark_type: str,
    curve: str | None = None,
    format_time_unit: str = "",
    visibility_time_unit: str | None = None,
    label_anchor_index: int = 0,
    domain_values: list[Any] | None = None,
    outer_chart_width: float | None = None,
    plot_width: float | None = None,
    *,
    panel_fields: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any], DetectedTimeUnit]:
    """Return (vl_type, merged_ax_vl, detected_time_unit) for a cartesian x encoding.

    Composes the ``time_unit_detect`` helpers rather than reimplementing them.

    ax_vl is never mutated — a new dict is always returned.
    detected_time_unit is the resolved time_unit (authored or auto-detected),
    or None when no bucketing applies. Callers use it to decide timeUnit emission.

    mark_type distinguishes bar/column (ordinal bucketed bands, gated by
    ``chart_rendering.type_inference.max_ordinal_buckets``) from line/area/scatter
    (always continuous temporal for a BUCKETED_CALENDAR_UNITS grain — a
    scatter point needs its real continuous position, not a band-snapped one)
    when no authored type forces the decision either way. All three still
    share bar's per-bucket gridline/label cost and the same scaffold-budget
    gate (``resolve_cartesian_x_type``) that drops an over-budget grain
    before it reaches here. ``curve`` is the
    authored line/area curve style; a band-aware ``step`` curve requires a band
    (nominal/ordinal) x-scale (see ``step_band.py``) unconditionally, at any
    bucket count — it overrides both the line/area always-temporal rule and
    bar's density gate. ``step-before``/``step-after`` do not force a band
    scale — they render as literal VL step interpolation on the resolved axis.

    visibility_time_unit is the render-local label-thinning grain. It never
    changes the encoding, resolved label-format time unit, or tick cadence.
    A resolved label-format time unit coarser than the encoding grain does set
    the default ticks to the same calendar openers.
    label_anchor_index identifies the first visible ordinal label so it keeps
    year context after automatic thinning.

    domain_values is the x scale's full band domain when it is wider than
    ``data`` alone implies — a chart whose overlay layers extend past the base
    series (``overlay_x_domain_values``). Vega-Lite unions the sub-layer
    domains, so the tick values must be derived from that union and THEN
    thinned to the label cadence; deriving them from the base's rows leaves
    every extra band unlabeled. Unset means the base's rows are the domain.

    outer_chart_width is the card's own outer pixel width — the same basis
    ``typography.width_tier`` classifies — used only to pick the sub-day
    clock vocabulary's narrow-width fallback (rule 4 of the time-notation
    vocabulary). Unset treats the card as not narrow.

    plot_width is the horizontal space known to Python before the spec is
    compiled — the card width minus chrome our OWN code already reserves
    (endpoint rail, reserved axis space — the caller already subtracted
    this before ``resolve_axis_x_overlap``), not the real plot rectangle
    vl_convert's ``autosize: fit`` will draw into (that also subtracts the
    y-axis's own rendered chrome, a data-dependent amount only Vega's
    layout engine measures — see ``predicted_tick_count`` in
    ``time_unit_detect.py``). It feeds the sub-day clock vocabulary's
    tick-cadence gate ONLY when nothing authored a tick cadence — an
    authored ``ticks.count``/``ticks.time_unit`` is read straight off the
    axis's own resolved cadence instead, with no width or prediction
    involved. Unset falls back to outer_chart_width (no known chrome to
    subtract); both unset disables the width-predicted half of the
    vocabulary for a genuinely continuous sub-day axis with no authored
    cadence (no basis to predict from — see
    ``default_subday_label_expr_for``).
    """
    from dbt_charts.core.compile.resolve.style.typography import width_tier
    from dbt_charts.core.render.chart.step_band import BAND_STEP_CURVE
    from dbt_charts.core.render.chart.time_unit_detect import (
        BUCKETED_CALENDAR_UNITS,
        default_label_expr_for,
        default_subday_label_expr_for,
        enumerated_axis_values,
        label_opener_values,
        ordinal_axis_values,
        predicted_tick_count,
        predicted_tick_count_ceiling,
        resolve_label_time_unit,
    )

    vl_type, x_type_from_data, time_unit = resolve_cartesian_x_type(
        data, x_field, axis, mark_type, curve == BAND_STEP_CURVE, panel_fields
    )
    from dbt_charts.core.render.chart.vl_field_maps import _n

    label_values: list[Any] | None = _n(axis, "labels", "values")

    # label_tu is the resolved label VOCABULARY — what a visible tick's text
    # says (fed to default_label_expr_for's format_time_unit param below).
    # authored_label_grain is a different question: whether the AUTHOR asked
    # for a coarser tick grain via labels.time_unit, which is what decides
    # whether the axis genuinely re-grains (label_tick_cadence, below). The
    # two agree whenever nothing was authored — but the cadence ladder can
    # promote label_tu to "year" as a render-local vocabulary decision (every
    # visible tick is a January; see resolve_temporal_label_visibility)
    # without the author having asked for a coarser grain at all, so using
    # label_tu for the grain question would wrongly re-grain (and, on a bar
    # axis, drop the monthly tick marks _LABEL_THINNING_TICK_MARK_TYPES exists
    # to restore) whenever the ladder — not the author — reaches year.
    label_tu: str | None = None
    authored_label_tu: str | None = None
    authored_label_grain: str | None = None
    if time_unit and time_unit in BUCKETED_CALENDAR_UNITS:
        authored_label_tu = getattr(getattr(axis, "labels", None), "time_unit", None)
        authored_label_grain = resolve_label_time_unit(time_unit, authored_label_tu)
        label_tu = format_time_unit if format_time_unit else authored_label_grain

    fiscal_year_start_month = axis.fiscal_year_start_month
    all_axis_values = (
        (
            domain_values
            if domain_values is not None
            else ordinal_axis_values(data, x_field)
        )
        if time_unit in BUCKETED_CALENDAR_UNITS
        else None
    )
    # label_cadence_values feeds only the label-opener/anchor computation
    # below, never the ordinal ticks/values injection further down (which
    # must stay `all_axis_values` — real per-row buckets, one band each).
    # On the temporal path gap-fill may have skipped scaffolding a row for
    # every bucket (continuous scale needs no filler row), so an "opener"
    # bucket for a coarser label cadence (e.g. a month label over weekly
    # data) can be absent from `data` even though the domain spans it.
    # Enumerate the full calendar span instead so no represented period
    # silently loses its label tick.
    label_cadence_values = (
        enumerated_axis_values(all_axis_values, time_unit, fiscal_year_start_month)
        if vl_type == "temporal"
        and time_unit in BUCKETED_CALENDAR_UNITS
        and all_axis_values
        else all_axis_values
    )
    label_tick_cadence = (
        authored_label_grain in BUCKETED_CALENDAR_UNITS
        and authored_label_grain != time_unit
        and axis.ticks.count is None
        and axis.ticks.time_unit is None
        and "values" not in ax_vl
    )
    label_tick_values: list[Any] = []
    if (
        label_tick_cadence
        and authored_label_grain is not None
        and time_unit is not None
        and label_cadence_values
    ):
        label_tick_values = (
            label_opener_values(
                label_cadence_values,
                time_unit,
                authored_label_grain,
                fiscal_year_start_month,
            )
            or label_cadence_values[:1]
        )

    temporal_anchor_value = ""
    anchor_visibility: str | None = None
    if time_unit in BUCKETED_CALENDAR_UNITS and label_tu in BUCKETED_CALENDAR_UNITS:
        anchor_visibility = visibility_time_unit or label_tu
        anchor_values = label_tick_values or label_cadence_values
        if anchor_values:
            anchor_openers = label_opener_values(
                anchor_values,
                time_unit,
                anchor_visibility,
                fiscal_year_start_month,
            )
            temporal_anchor_value = str((anchor_openers or anchor_values[:1])[0])

    # labels.values needs the epoch-ms membership filter to match every
    # listed date exactly. On a continuous temporal scale datum.value is
    # already epoch ms — always exact. On any other emit path datum.value is
    # the raw domain value, which Vega parses with JS Date semantics: only
    # date-only ISO forms (YYYY-MM, YYYY-MM-DD) parse as UTC midnight.
    # Anything else — nominal/quantitative values, non-ISO bucket labels like
    # "Q1 2024", naive datetime strings like "2024-01-01T00:00:00" (LOCAL
    # time in JS) — would silently blank every label in non-UTC runtimes.
    # Validate against the same stringified form the ordinal enrichment emits
    # (date/datetime objects included) and raise loudly. No magic.
    if label_values is not None and vl_type != "temporal":
        from dbt_charts.core.render.chart.time_unit_detect import (
            _ordinal_axis_iso_value,
        )

        for row in data:
            if x_field not in row or row[x_field] is None:
                continue
            v = _ordinal_axis_iso_value(row[x_field])
            if not (isinstance(v, str) and _ISO_UTC_SAFE_RE.match(v)):
                from dbt_charts.core.diagnostics.chart_data import ChartDataError
                from dbt_charts.core.diagnostics.codes_render import (
                    ERR_LABEL_VALUES_NOT_TEMPORAL,
                )

                raise ChartDataError.from_code(
                    ERR_LABEL_VALUES_NOT_TEMPORAL,
                    field=x_field,
                    cause=f"it contains {row[x_field]!r}, which isn't a UTC-safe ISO date",
                    remedy=(
                        "Use date-only ISO dates (YYYY-MM-DD or YYYY-MM) or date "
                        "objects in the query, or remove labels.values."
                    ),
                )

    result = dict(ax_vl)
    label_angle = result.get("labelAngle")
    steep_tilt = isinstance(label_angle, (int, float)) and abs(label_angle) >= 90

    # Ordinal time-format routing: d3-time-format strings on non-temporal axes must
    # become utcFormat(toDate(datum.value), ...) labelExpr — raw `format` on an ordinal
    # axis is interpreted by d3-format (number format), not d3-time-format, so time
    # directives like %b or %Y silently produce garbage. Route early so subsequent
    # ordinal enrichment (smart labelExpr) respects an already-authored expr.
    if vl_type != "temporal":
        fmt = result.get("format")
        if isinstance(fmt, str) and is_time_format(fmt):
            if "labelExpr" not in result:
                result["labelExpr"] = _utc_time_label_expr(fmt)
            result.pop("format")

    # "nominal" only ever reaches here for heatmap's grid axis (the
    # mark_type == "heatmap" branch in resolve_cartesian_x_type) — it never
    # produces "nominal" for bar's own resolution.
    if (
        vl_type in ("ordinal", "nominal")
        and time_unit
        and time_unit in BUCKETED_CALENDAR_UNITS
    ):
        visibility_thinned = (
            visibility_time_unit in BUCKETED_CALENDAR_UNITS
            and visibility_time_unit != authored_label_grain
        )
        # Bar/histogram get the missing-positional-cue restoration
        # (_LABEL_THINNING_TICK_MARK_TYPES, below): `values` keeps every
        # bucket and `ticks: True` draws them, so a render-local promotion
        # never re-grains the axis. Every other mark type reaching this
        # branch — in practice, heatmap's grid, plus line/area/scatter with
        # an authored ordinal x-axis (`curve: step`, `axis_x.type: ordinal`,
        # or a non-Jan fiscal year start) — has no such restoration:
        # collapsing `values` to the render-local thinned grain keeps ticks
        # and labels in lockstep instead of a tick under every unlabeled band.
        restores_ticks = mark_type in _LABEL_THINNING_TICK_MARK_TYPES
        if "values" not in result:
            # `| None`: `all_axis_values` (below) is None when the field has
            # no non-null values at all (`ordinal_axis_values` — empty data),
            # a real, exercised case (`test_no_data_no_values_injected`), not
            # dead code — `if tick_values:` below correctly leaves `values`
            # unset in that case rather than injecting an empty list.
            tick_values: list[Any] | None  # type-state: explicit_any — raw row type
            if label_tick_cadence:
                tick_values = label_tick_values
            elif (
                visibility_thinned
                and not restores_ticks
                and visibility_time_unit is not None
                and label_cadence_values
            ):
                tick_values = (
                    label_opener_values(
                        label_cadence_values,
                        time_unit,
                        visibility_time_unit,
                        fiscal_year_start_month,
                    )
                    or label_cadence_values[:1]
                )
            else:
                tick_values = all_axis_values
            if tick_values:
                result["values"] = tick_values

        if restores_ticks and (label_tick_cadence or visibility_thinned):
            result["ticks"] = True

        # Apply the smart cadence labelExpr whenever the grain was derived from
        # date-like data (temporal OR ordinal bucket strings like "2025-01").
        # Nominal data with a merely-authored time_unit ("Core"/"Growth") stays
        # unformatted. V1 normalized bucket strings to ISO dates first, so it
        # reached this via the temporal path; V2 reads the raw strings.
        if (
            x_type_from_data in ("temporal", "ordinal")
            and "format" not in result
            and "labelExpr" not in result
        ):
            smart_expr = default_label_expr_for(
                time_unit,
                label_tu,
                visibility_time_unit,
                fiscal_year_start_month,
                anchor_index=(
                    0 if label_tick_cadence or "values" in ax_vl else label_anchor_index
                ),
                anchor_value="" if "values" in ax_vl else temporal_anchor_value,
                # This is the ordinal/bucket branch: `datum.value` is always a
                # real per-row bucket key (never a Vega-computed continuous
                # tick), so ticks_are_buckets stays True unconditionally —
                # that is what keeps the yearweek day-shift rule inside
                # default_label_expr_for off for bar/histogram, thinned or
                # not (see its docstring).
                ticks_are_buckets=True,
                # The anchor must compare at whichever grain `values` (above)
                # actually landed at: the authored grain when `values`
                # collapsed to `label_tick_values`, the render-local
                # visibility grain when it collapsed to visibility openers,
                # or the encoding grain when it kept every native bucket
                # (restores_ticks, or no thinning at all) — comparing at a
                # coarser grain than `values` holds would match every tick in
                # that period (e.g. all 12 months of the anchor's year), the
                # same bug the continuous-temporal branch below guards
                # against via its own encoding-grain comparison.
                anchor_grain=(
                    authored_label_grain
                    if label_tick_cadence
                    else (
                        visibility_time_unit
                        if (visibility_thinned and not restores_ticks)
                        else time_unit
                    )
                ),
                steep_tilt=steep_tilt,
            )
            if smart_expr is not None:
                result["labelExpr"] = smart_expr

    elif vl_type == "temporal" and time_unit and time_unit in BUCKETED_CALENDAR_UNITS:
        # Tracks which grain `values` actually landed at when this branch
        # injects them, so the anchor comparison below can match it exactly
        # (see anchor_grain on the default_label_expr_for call). None means
        # this branch injected nothing — either `values` pre-existed in
        # ax_vl (an authored explicit list) or no thinning applies — and the
        # anchor falls back to the two-way ticks_are_buckets choice.
        injected_values_grain: str | None = None
        if "values" not in result:
            if label_tick_cadence and label_tick_values:
                # A coarser display grain uses source openers so short domains
                # keep every represented period (for example, a six-week
                # Jan–Feb domain gets both month ticks rather than only
                # Vega's interior Feb tick). Visibility thinning remains
                # independent and never changes these.
                result["values"] = label_tick_values
                injected_values_grain = authored_label_grain
            elif (
                visibility_time_unit in BUCKETED_CALENDAR_UNITS
                and visibility_time_unit != time_unit
                and axis.ticks.count is None
                and axis.ticks.time_unit is None
                and label_cadence_values
            ):
                # Render-local (ladder) thinning, never authored: Vega's own
                # tick generator places ticks only on true calendar
                # boundaries within the domain (e.g. Feb 1), so a domain
                # start that isn't itself a calendar boundary (a weekly
                # series opening Jan 5) never gets a tick at all — not
                # merely hidden, absent from the DOM. Injecting the real
                # opener values (including the domain-start one) keeps the
                # leading label, and its year context, on screen.
                result["values"] = (
                    label_opener_values(
                        label_cadence_values,
                        time_unit,
                        visibility_time_unit,
                        fiscal_year_start_month,
                    )
                    or label_cadence_values[:1]
                )
                injected_values_grain = visibility_time_unit
        # The continuous half of the _LABEL_THINNING_TICK_MARK_TYPES
        # restoration: a bar past `max_ordinal_buckets` resolves temporal, so
        # the ordinal branch above never sees it — yet that is where the cue
        # matters most (densest bars, sparsest labels). `values` above already
        # collapsed to the label cadence, so this marks one period, not one
        # bucket. Gated on THIS branch having injected them, which is what an
        # authored cadence (`ticks.count`, `ticks.time_unit`, explicit `values`)
        # opts out of. `ticks.visible` is NOT an opt-out: it arrives already
        # cascaded, so an authored `false` is indistinguishable from the bar
        # family's own `false` — overriding that default is this guard's job.
        # The ordinal branch above overrides it the same way.
        if (
            injected_values_grain is not None
            and mark_type in _LABEL_THINNING_TICK_MARK_TYPES
        ):
            result["ticks"] = True
        # Temporal escape-hatch with a bucketed time_unit: emit smart labelExpr so the
        # axis reads human-friendly cadence labels (e.g. "Jan 2024") instead of the
        # raw ISO tick values that Vega emits for utc temporal domains. An authored
        # format is a native d3-time-format on a temporal encoding — it wins outright,
        # same as the ordinal branch's "format" not in result guard.
        #
        if "labelExpr" not in result and "format" not in result:
            smart_expr_t = default_label_expr_for(
                time_unit,
                label_tu,
                visibility_time_unit,
                fiscal_year_start_month,
                anchor_index=0,
                anchor_value=(
                    ""
                    if "values" in ax_vl
                    # Use a calendar-semantic date anchor when the effective
                    # visibility grain differs from the encoding grain.
                    # When they match, datum.index === 0 is simpler and equally
                    # correct for the domain-start tick.
                    else (
                        temporal_anchor_value
                        if anchor_visibility and anchor_visibility != time_unit
                        else ""
                    )
                ),
                # True whenever this branch injected explicit `values` above:
                # `datum.value` is then one of our own real per-row bucket
                # dates, not a tick Vega computed itself, so the yearweek
                # Sunday-anchor correction below must stay off (it would
                # shift an already-exact date to a real date nothing was
                # plotted on). False only when no explicit `values` exists
                # and Vega's own continuous tick generator is still in play.
                ticks_are_buckets="values" in result,
                # Mirrors the ordinal branch above: the anchor must compare
                # at whichever grain `values` actually landed at just above —
                # the authored grain when it collapsed to `label_tick_values`,
                # the render-local visibility grain when it collapsed to
                # visibility openers. `None` (no thinning injected here)
                # falls back to the two-way ticks_are_buckets choice, which
                # already covers pre-existing authored `values` and the
                # genuinely continuous, un-thinned case correctly.
                anchor_grain=injected_values_grain,
                steep_tilt=steep_tilt,
            )
            if smart_expr_t is not None:
                result["labelExpr"] = smart_expr_t
    # Step-anchored cadence (axis.ticks.time_unit) requires a genuinely
    # continuous temporal x-axis — compile-time gates (_bake_cartesian_axes)
    # already reject it on axis_y, but whether THIS axis resolves to temporal
    # vs ordinal is a data-dependent decision only known here. Unlike count
    # (which silently no-ops on ordinal — see the density-gate test suite),
    # time_unit raises: the whole point of the surface is an explicit cadence,
    # so a silent no-op would hide a misconfiguration rather than surface it.
    # Deliberately NOT moved into apply_x_tick_cadence alongside the step gate:
    # this check is unchanged from before that helper existed, and the emitters
    # that bypass this function have always ignored time_unit. Widening its
    # reach here would newly reject boards that render on main today.
    if axis.ticks.time_unit is not None and vl_type != "temporal":
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import (
            ERR_TICKS_INTERVAL_NOT_TEMPORAL,
        )

        raise ChartDataError.from_code(
            ERR_TICKS_INTERVAL_NOT_TEMPORAL, field=x_field, vl_type=vl_type
        )

    # Authored count/step, plus the bare-step gate — both need the resolved
    # vl_type, a data-dependent answer no compile-time check can reach.
    apply_x_tick_cadence(result, axis, x_field, vl_type)

    # The same shape for labels.format, which is why it sits alongside: only
    # the data says whether this axis's field can carry a number spec. A
    # heatmap needs its own remedy — it has no measure axis for the default
    # text to point at.
    gate_label_format(
        axis.labels.format,
        x_field,
        data,
        vl_type,
        setting="axis_x.labels.format",
        remedy=HEATMAP_FORMAT_REMEDY if mark_type == "heatmap" else None,
    )

    # The temporal-only half: ticks.time_unit becomes VL's own
    # axis.tickCount: {interval, step} (VL's wire format says "interval"
    # regardless of dbt charts' field name), and absent any authored cadence
    # the resolved label grain supplies a default one. Both need the label
    # grain, which is why they stay here rather than in apply_x_tick_cadence.
    if vl_type == "temporal" and "tickCount" not in result and "values" not in result:
        _ticks_time_unit = axis.ticks.time_unit
        if _ticks_time_unit is not None:
            if _ticks_time_unit not in _TEMPORAL_TICK_INTERVAL:
                from dbt_charts.core.diagnostics.chart_data import ChartDataError

                raise ChartDataError(
                    f"ticks.time_unit: {_ticks_time_unit!r} has no step-anchored "
                    "cadence — only a calendar-bucketing grain "
                    f"({', '.join(_TEMPORAL_TICK_INTERVAL)}) names a VL tick "
                    "interval."
                )
            interval, _ = _TEMPORAL_TICK_INTERVAL[_ticks_time_unit]
            tick_count: dict[str, Any] = {"interval": interval}
            if axis.ticks.step is not None:
                tick_count["step"] = axis.ticks.step
            result["tickCount"] = tick_count
        else:
            tick_time_unit = label_tu if label_tu is not None else time_unit
            if tick_time_unit in _TEMPORAL_TICK_INTERVAL:
                interval, step = _TEMPORAL_TICK_INTERVAL[tick_time_unit]
                result["tickCount"] = {"interval": interval, "step": step}

    # Genuinely continuous temporal (no calendar-bucketed grain applies) —
    # the sub-day clock vocabulary lives here, after cadence resolution
    # above, rather than beside the BUCKETED_CALENDAR_UNITS branches earlier
    # in this function, because it answers a different question (clock
    # rules, not calendar-opener rules) and its gate needs the axis's
    # RESOLVED tick cadence — the same `result["tickCount"]` the cadence
    # block above just finished writing (or left absent). Continuous
    # line/bar/scatter axes already clear tick collisions on their own
    # (Vega-Lite's overlap avoidance) at every width tested — this only
    # supplies the label vocabulary, never tick positions.
    if vl_type == "temporal" and time_unit is None:
        if "labelExpr" not in result and "format" not in result:
            # An authored scale domain is what Vega actually renders across —
            # cartesian_x_scale_domain applies axis.scale.continuous.domain to
            # the compiled spec AFTER this function returns, so the gate must
            # read it here or it decides span/midnight-count against the
            # data's own extent while a wider (or narrower) authored domain
            # silently renders something else entirely (e.g. two distinct
            # calendar days both reading bare "Midnight" with no date row).
            authored_domain = (
                axis.scale.continuous.domain
                if axis.scale is not None and axis.scale.continuous is not None
                else None
            )
            subday_values = (
                domain_values
                if domain_values is not None
                else [row.get(x_field) for row in data]
            )
            resolved_tick_count = result.get("tickCount")
            subday_tick_count_ceiling: int | None
            if isinstance(resolved_tick_count, int):
                # Authored ticks.count: the axis's real tick count, exact —
                # no floor/ceiling split needed.
                subday_tick_count: int | None = resolved_tick_count
                subday_tick_count_ceiling = resolved_tick_count
            elif isinstance(resolved_tick_count, dict):
                # Authored ticks.time_unit: _TEMPORAL_TICK_INTERVAL's finest
                # entry is "day", so this axis's ticks always land on local
                # midnight or coarser — the vocabulary never applies.
                subday_tick_count = None
                subday_tick_count_ceiling = None
            else:
                available_width = (
                    plot_width if plot_width is not None else outer_chart_width
                )
                subday_tick_count = predicted_tick_count(available_width)
                subday_tick_count_ceiling = predicted_tick_count_ceiling(
                    available_width
                )
            subday_expr = default_subday_label_expr_for(
                subday_values,
                _n(axis, "labels", "clock"),
                narrow=(
                    outer_chart_width is not None
                    and width_tier(outer_chart_width) == "tiny"
                ),
                tick_count=subday_tick_count,
                tick_count_ceiling=subday_tick_count_ceiling,
                authored_domain=authored_domain,
            )
            if subday_expr is not None:
                result["labelExpr"] = subday_expr

    # Case injection runs last so it wraps any temporal smart-cadence labelExpr
    # that was set above (test: test_temporal_yearmonth_axis_upper_wraps_smart_cadence_expr).
    from dbt_charts.core.render.chart.vl_field_maps import (
        inject_axis_label_case,
        inject_axis_label_values_filter,
    )

    result = inject_axis_label_case(result, axis)
    # labels.values filter runs last of all — it wins over any smart-cadence
    # or case-transformed text already resolved above.
    result = inject_axis_label_values_filter(result, axis)
    return vl_type, result, time_unit


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


def y_zero_scale(
    axis: ResolvedAxisStyle | None,
) -> dict[str, Any]:  # type-state: explicit_any — foreign VL scale JSON
    """Build a VL y-scale dict from the resolved y-axis style.

    Merges every scale field (``type``, ``base``, ``exponent``, etc.) via
    ``emit_resolved_scale_vl`` — one canonical emitter, so a measure-axis
    ``scale.type: log`` (previously honored only on scatter) reaches
    line/area/bar too. ``zero`` is excluded from that merge and computed
    separately:

    axis.scale.zero is True  → {"domainMin": <pinned floor>, "zero": True}, or
                               {"nice": False, "zero": True} when no rung
                               supplies a floor
    anything else            → {"zero": False}

    The ladder comes off ``axis`` and is filtered by
    ``zero_anchor_pinned_floor`` — callers do not pre-filter it.
    """
    scale = getattr(axis, "scale", None) if axis is not None else None

    out: dict[str, Any] = {}
    if scale is not None:
        out.update(emit_resolved_scale_vl(scale))
        out.pop("zero", None)  # zero is computed below, not passed through raw

    if is_zero_anchored(scale):
        # Only pin a floor a rung actually supplies. With no ladder,
        # zero_anchor_floor's literal 0.0 is a floor only while the data is
        # non-negative — on all-negative data it pins 0 as the BOTTOM of the
        # domain, which is degenerate and puts every mark on one pixel row.
        #
        # `nice: False` goes with it. An explicit domainMin is what suppressed
        # Vega-Lite's default `nice: true` on a continuous scale; dropping the
        # pin alone silently hands the top edge back to nice-rounding (an
        # exact 103 becomes 110), which moves every positive-data domain on a
        # theme that bakes no ladder and desynchronizes the render from
        # effective_measure_domain, whose whole contract is to never predict
        # where `nice` lands.
        # `scale` is not None here, so `axis` is not None either.
        assert axis is not None
        pinned = zero_anchor_pinned_floor(axis)
        if pinned is not None:
            out["domainMin"] = pinned
        else:
            # setdefault, not assignment: `nice` is an authorable key that
            # emit_resolved_scale_vl already forwarded above, and an author
            # asking for a rounded top must keep it.
            out.setdefault("nice", False)
        out["zero"] = True
    else:
        out["zero"] = False
    return out


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
