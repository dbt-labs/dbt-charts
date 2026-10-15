"""Detector: WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Covered families: bar (vertical + horizontal, single-metric + multi-metric),
line (single-metric + multi-metric), and area (single-metric + multi-metric).
Heatmap and scatter are NOT covered — both carry the identical UTC/local
off-by-one hazard (confirmed via direct emitter reproduction), but their
emitter paths diverge structurally from bar/line/area; a follow-up task
should implement their coverage.

Detection rule:
  (a) chart.style.axis_x.labels.expr contains a bare local-time accessor
      (timeFormat|year|quarter|month|date|day|hours|week|dayofyear -- not their
      utc-prefixed siblings) outside a string literal, AND
  (b) resolve_cartesian_x_type() returns a calendar grain in
      BUCKETED_CALENDAR_UNITS for this chart's x field, AND
  (c) EITHER vl_type == "temporal" (VL emits a utcX timeUnit transform so
      datum.value is a UTC-midnight Date -- all local-time accessors read it
      in local time)
      OR the x-field data contains at least one value that normalizes to a
      date-only ISO string (YYYY-MM or YYYY-MM-DD) -- the emitted bucket form
      that JS Date.parse treats as UTC midnight -- subject to:
        * `timeFormat`: also requires "toDate(" in the expr (timeFormat on a
          raw ISO string renders 0NaN in both TZs -- wrong but TZ-invariant).
        * date-part accessors (year/month/quarter/date/day/...): no toDate()
          requirement -- they do their own new Date() coercion internally and
          ARE TZ-dependent on a date-only ISO domain without an explicit
          toDate().

Condition (c) distinguishes the real hazard from a merely-broken expression:
a `timeFormat` call on an ordinal axis without toDate() has datum.value = ISO
string -- timeFormat cannot parse it and renders 0NaN in both TZs; silence is
correct there. `month(datum.value)` on the same ordinal axis with date-only
ISO strings coerces the string to a Date internally and reads the month in
local time -- the real hazard, no toDate needed.

The detector mirrors each emitter's preprocessing pipeline exactly:

  bar/line/area, wide or not:
    1. normalize_labeled_temporal (bar.py:427, line.py:92, area.py:96)
    2. gap_fill_ordinal_time_per_panel (bar.py:453, line.py:97, area.py:101)
       — every emitter regroups its (possibly normalize-mutated) rows into
       panels via ``regroup()``/``restripe()`` before gap-fill, so the
       detector does the same rather than calling the flat
       ``gap_fill_ordinal_time`` directly on the whole (cross-panel-pooled)
       row list.
    3. resolve_cartesian_x_type

  A wide (y: [a, b]) chart's pipeline is identical to a regular chart's --
  gap_fill_ordinal_time_per_panel runs before the wide/long-form dispatch in
  every emitter, called with chart.color (the authored dimension a wide
  chart's measures are grouped by, or None), so the dim-cross-join fills the
  same (bucket, dimension) cells for both shapes; there is no separate
  wide-only preprocessing branch left to mirror.

The _ISO_UTC_SAFE_RE gate mirrors _ordinal_bucket_key (complete_ordinal_time_series
canonicalizes x to date-only ISO; '2024-04-15 00:00:00' → '2024-04-15'), same
for wide and regular charts.

Reading the bucketing *decision* (not the emitted Vega-Lite spec) is
deliberate: emitters/bar.py routes many bucketed grains to Vega-Lite's
*ordinal* scale (density gate, yearweek/yearmonthdate, non-January fiscal
year), which emits no ``encoding.x.timeUnit`` at all -- yet condition (c)
gates correctly on vl_type + toDate for each case.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
)
from dbt_charts.core.diagnostics import (
    WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS,
    Diagnostic,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    _ordinal_bucket_key,
    detect_time_unit,
)
from dbt_charts.core.render.chart.type_inference import (
    _ISO_UTC_SAFE_RE,
)
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.x_axis import emitted_x_axis

# Bare local-time accessors that read datum.value in host local time; their
# utc-prefixed siblings (utcFormat/utcyear/utcmonth/utcdate/utcquarter/...) share
# no word boundary with these names so the \b anchor excludes them.
_LOCAL_TIME_ACCESSOR_RE = re.compile(
    r"\b(?P<accessor>timeFormat|year|quarter|month|date|day|hours|week|dayofyear)\s*\("
)

# Strip single- and double-quoted string literals before matching; prevents false
# positives from accessor-shaped substrings inside labels like ' month(s) in' or
# " month(s) in".
_QUOTED_STRING_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose axis_x label expr uses local time
    on a bucketed axis in a way that produces TZ-dependent renders.

    Bar (vertical and horizontal, single-metric and multi-metric) and line/area
    (single-metric and multi-metric) route their x-axis through
    resolve_cartesian_x_type -- the same function their emitters call to decide
    the x encoding. Heatmap and scatter are NOT covered (see module docstring).
    """
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(
            chart, ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart
        ):
            continue
        if chart.chart_type == "histogram":
            # Histogram emits quantitative-binned x (no timeUnit transform,
            # no calendar bucketing) -- calendar-grain warnings never apply.
            continue
        if chart.x is None:
            continue
        expr = chart.style.axis_x.labels.expr
        if expr is None:
            continue

        # Strip string literals once; use stripped form for both the accessor
        # match and the toDate check to avoid false positives from accessor-
        # shaped text inside quoted values.
        stripped_expr = _QUOTED_STRING_RE.sub("", expr)
        m = _LOCAL_TIME_ACCESSOR_RE.search(stripped_expr)
        if m is None:
            continue
        if chart_id not in ctx.chart_results:
            continue

        # The chart's x pipeline failed (not a date column, or rows that don't
        # regroup): skip this chart without dropping others.
        try:
            axis = emitted_x_axis(chart, ctx.chart_results[chart_id])
        except (ValueError, ChartDataError):
            continue
        vl_type, detected_tu, pipeline_rows = axis.vl_type, axis.time_unit, axis.rows
        if (
            detected_tu is None
            and vl_type == "temporal"
            and chart.style.axis_x.time_unit is None
        ):
            # The scaffold-budget gate drops an over-budget grain when it flips a
            # bar onto a continuous time scale; the hazard is in the domain's
            # date-only ISO values, not in whether the axis bands, so recover it.
            try:
                detected_tu = detect_time_unit(
                    [row.get(chart.x) for row in pipeline_rows if chart.x in row]
                )
            except ValueError:
                continue
        if detected_tu is None or detected_tu not in BUCKETED_CALENDAR_UNITS:
            continue
        # Horizontal bar never emits a temporal x encoding (no timeUnit transform).
        # datum.value is always a raw ISO string. The bar density gate returns
        # "temporal" above MAX_ORDINAL_BUCKETS, which would fire a false positive on
        # bare timeFormat without toDate -- force ordinal so the toDate check below
        # is the only hazard gate.
        # Wide vertical bar is NOT overridden: _emit_vertical resolves x with the
        # same density gate as narrow bar and CAN emit temporal.
        if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal":
            vl_type = "ordinal"
        accessor = m.group("accessor")
        if vl_type != "temporal":
            # timeFormat on a raw ISO string renders 0NaN in both TZs -- wrong
            # but TZ-invariant. Only toDate() promotes the string to a UTC-midnight
            # Date, after which timeFormat reads it in local time (the actual hazard).
            # Date-part accessors (year/month/quarter/date/day/...) do their own
            # internal new Date() coercion -- they don't need an explicit toDate()
            # and ARE TZ-dependent on a date-only ISO domain regardless.
            if accessor == "timeFormat" and "toDate(" not in stripped_expr:
                continue
            # Gate on the emitted bucket shape for all ordinal cases:
            # _ordinal_bucket_key rewrites datetime strings to date-only ISO
            # -- '2024-04-15 00:00:00' → '2024-04-15' -- same for wide and
            # regular charts, since complete_ordinal_time_series canonicalizes
            # x for both.
            x_values = (row.get(chart.x) for row in pipeline_rows if chart.x in row)
            if not any(
                _ISO_UTC_SAFE_RE.match(_ordinal_bucket_key(v))
                for v in x_values
                if v is not None
            ):
                continue
        warnings.append(
            Diagnostic.from_code(
                WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS,
                chart=chart_id,
                path=f"charts.{chart_id}.style.axis_x.labels.expr",
                field=chart.x,
                message=WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.message_template.format(
                    chart_id=chart_id, time_unit=detected_tu, accessor=accessor
                ),
                fix=WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.fix_template,
            )
        )

    return warnings
