"""Detector: WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart is a ResolvedBarChart or ResolvedAreaChart AND
  its resolved stack mode is "normalize" AND
  its baked measure format carries a percent sign AND
  the raw y values in some stack group do NOT already sum to ~1.

A normalized stack pins its own measure-axis format (``pin_normalize_axis_format``,
emitters/_cartesian.py), so the format the author wrote never reaches the axis.
It reaches the hover rows (``features/structured_tooltip.py``, via
``style.tooltip_format``), the printed value labels and the stack-total label
(``_label_format_fallback``, resolve/chart/bar.py and area.py) — every one of
which reads the RAW column value. Under a percent format a count of 20 prints
as ``2000%``.

The sums-to-1 predicate is what separates a lie from a correct chart: when each
group's raw values already sum to 1, the raw value IS the painted share and the
percent format is honest — the shape `examples/ai_spend` and `examples/rockets`
ship, which gating on ``stack == "normalize"`` alone would report.

Reading ``style.axis_y.labels.format`` covers BOTH authoring doors with one
read: ``style.number_format`` / a chart's ``format:`` reach it through the axis
cascade's chart-format layer, and ``style.axis_y.labels.format`` is authored
onto it directly. The theme default is an SI spec (``.3~s``), never a percent,
so a percent format here is always one the author wrote.

Only a percent format fires: a currency or plain-digit format prints the true
raw number and differs from the axis only in unit, which is a legitimate thing
to author.

Groups are (panel, x): a normalized stack normalizes within a facet panel, so
merging panels would sum one x across all of them and report a faceted chart
whose every panel is a real share. Overlay layers are not judged; they carry
their own measure and do not join the base chart's stack.
"""

from __future__ import annotations

from collections import defaultdict

from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup, restamp
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_VALUE_FIELD,
    unfold_wide_rows,
    wide_measure_fields,
)
from dbt_charts.core.diagnostics import (
    WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE,
    Diagnostic,
)
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.utils import CellValue, Rows, coerce_numeric_cell

_NormalizableChart = ResolvedBarChart | ResolvedAreaChart

# How far a group's total may sit from 1 and still read as a share. A share
# rounded for display accumulates its error per segment, not per group: N
# segments rounded to two decimals reach 0.005 * N, so ten of them can total
# 1.05. Generous on purpose — a raw count lands orders of magnitude away, so
# widening this costs no detection and buys silence on honest rounded shares.
_SUMS_TO_ONE_TOLERANCE = 0.05

# What counts as a zero total, where a share is undefined. Float epsilon, not
# the tolerance above: signed values that cancel leave a remainder around
# 5e-17, while a per-mille measure summing to 0.008 is exactly the lie this
# detector is named for and must still be reported.
_ZERO_EPSILON = 1e-9


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per normalized chart whose percent-formatted
    measure is not already a share."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, (ResolvedBarChart, ResolvedAreaChart)):
            continue
        if chart.stack != "normalize":
            continue
        measure_format = chart.style.axis_y.labels.format
        if measure_format is None or "%" not in measure_format:
            continue
        if chart_id not in ctx.chart_results:
            continue
        measure = _measure(chart)
        if measure is None:
            continue
        field, value_field = measure
        total = _group_total_that_is_not_one(
            chart, value_field, ctx.chart_results[chart_id]
        )
        if total is None:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE,
                chart=chart_id,
                # The format is usually authored as `style.number_format`, not
                # here; this anchor leans on the candidate walk to land on
                # `style`, then the chart, when the axis key is absent.
                path=f"charts.{chart_id}.style.axis_y.labels.format",
                field=field,
                message=WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.message_template.format(
                    chart_id=chart_id,
                    format=measure_format,
                    field=field,
                    total=f"{total:g}",
                ),
                fix=WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.fix_template,
            )
        )

    return warnings


def _measure(chart: _NormalizableChart) -> tuple[str, str] | None:
    """The measure the format is applied to: (name for the message, value column).

    The two differ for a wide chart, which stacks its own measure list: resolve
    has already replaced ``chart.y`` with the synthetic fold field by the time
    this runs, so the message names the authored measures while the sum reads
    the folded column. None when the chart yields no measure name at all, which
    leaves nothing honest to put in the message.
    """
    if wide_fields := wide_measure_fields(chart):
        return ", ".join(wide_fields), WIDE_VALUE_FIELD
    return (chart.y, chart.y) if isinstance(chart.y, str) else None


def _group_total_that_is_not_one(
    chart: _NormalizableChart, value_field: str, rows: Rows
) -> float | None:
    """The first stack group's total that is not ~1, or None when every group
    the detector could judge sums to ~1.

    A group totaling zero is not judged: a share is undefined at a zero total,
    and zeros coerce to 0.0 rather than abstaining the way nulls do, so an
    honest share board with one empty x bucket would otherwise be reported for
    it. Zero is tested against a float epsilon rather than exactly, since signed
    values that cancel leave a tiny nonzero remainder.

    None is also the answer when nothing is judgeable at all — no x, no
    coercible value anywhere — which abstains rather than calling an empty or
    all-null chart a lie.
    """
    for total in _group_totals(chart, value_field, rows):
        if abs(total) > _ZERO_EPSILON and abs(total - 1.0) > _SUMS_TO_ONE_TOLERANCE:
            return total
    return None


def _group_totals(
    chart: _NormalizableChart, value_field: str, rows: Rows
) -> list[float]:
    """Each stack group's summed raw measure — one entry per (panel, x) that
    holds at least one coercible value.

    Signed, matching the Total row the tooltip prints (a plain `joinaggregate`
    sum, ``features/structured_tooltip.py``) rather than the painted stack
    height — an ``abs()`` here would stop measuring the number the warning is
    about. The trap that leaves: a group mixing signs can total ~1 out of values
    that are no kind of share (`+100`/`-99`) and go unreported.

    The split is the chart's own baked small-multiples partition. ``restamp``
    puts x and the color column back on a panel's rows for the case where
    either is itself the facet field: the split strips every partition column,
    and the wide fold drops a row whose color dimension is null.
    """
    x_field = chart.x
    if not isinstance(x_field, str):
        return []

    dataset = restamp(regroup(chart.panel_axes, rows), x_field)
    if chart.color is not None:
        dataset = restamp(dataset, chart.color)

    totals: list[float] = []
    for panel in dataset.panels:
        panel_rows = (
            unfold_wide_rows(panel.rows, chart.wide_measures, chart.color)
            if chart.wide_measures
            else panel.rows
        )
        sums: defaultdict[CellValue, float] = defaultdict(float)
        for row in panel_rows:
            x = row.get(x_field)
            value = coerce_numeric_cell(row.get(value_field))
            if x is None or value is None:
                continue
            sums[x] += value
        totals.extend(sums.values())
    return totals
