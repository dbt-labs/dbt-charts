"""Detector: WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart is a ResolvedPieChart with total.visible == True AND inner_radius > 0
  AND chart_id is present in chart_results
  AND a usable slot width exists (from vega_specs or chart.wheel_width)
  AND a usable slot height exists (from layout_chart_heights or, for
      attached-table wheels, board_spec.style.chart_defaults.view.continuous_height
      — the Vega default applied when the wheel is stamped with height=None)
  AND the formatted total text width > hole_diameter, where
  hole_diameter = 2 * pie_hole_radius_px(min(slot_width, slot_height),
                                         outer_fraction, inner_radius).

Width source: for normal VL donuts, the stamped slot width from
vega_specs[chart_id]["width"] minus the slice labels' reach on both sides
(arc_disk_width — the same model classification uses). Under autosize fit the
runtime view can shrink further for a legend; that deduction is deliberately
not modeled (the shared VL chrome estimator is a separate task), so the
computed hole is a slight upper bound and the check errs toward not firing.
chart.wheel_width for attached-table donuts (SVG path, absent from vega_specs).

Height source: layout_chart_heights[chart_id] (ResolvedLayoutItem.height, non-
sparse) for VL donuts; continuousHeight from the theme's chart_defaults.view for
attached-table donuts (the wheel is rendered with height=None, so Vega uses
config.view.continuousHeight rather than any authored or layout height).

The detector iterates ctx.layout_charts — the placement-matched chart instances
from the layout tree — rather than ctx.board_spec.charts (the catalog), so each
chart's geometry fields match the slot being judged even when the same id appears
at multiple placements.
"""

from __future__ import annotations

from decimal import Decimal

from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart.pie_attachment import (
    arc_disk_width,
    compute_shares,
    finalize_pie_label_lines,
)
from dbt_charts.core.diagnostics import WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS, Diagnostic
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.emitters.pie import pie_hole_radius_px
from dbt_charts.core.render.format_utils import format_value
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per donut whose formatted center total overflows the hole."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.layout_charts.items():
        if not isinstance(chart, ResolvedPieChart):
            continue
        if chart.total is None or not chart.total.visible:
            continue
        if chart.style.inner_radius <= 0:
            continue
        if chart_id not in ctx.chart_results:
            continue

        # Width and height differ by render path.
        if chart_id in ctx.vega_specs:
            raw_width = ctx.vega_specs[chart_id].get("width")
            if not isinstance(raw_width, int | float):
                continue
            rows = ctx.chart_results[chart_id]
            shares = compute_shares(chart.theta, rows)
            labels_style = chart.style.slice_mark.labels
            label_reach_lines: list[str] = []
            if labels_style is not None and shares:
                # identity_field is the resolved naming column — a conditional-mode
                # color channel paints, it doesn't name (resolve/chart/pie.py).
                label_reach_lines = [
                    line
                    for row_lines in finalize_pie_label_lines(
                        chart.theta, chart.identity_field, rows, labels_style, shares
                    )
                    if row_lines
                    for line in row_lines
                ]
            if labels_style is not None and label_reach_lines:
                lf = labels_style.font
                # The theme cascade guarantees a complete slice-label font.
                assert lf.family is not None and lf.size is not None
                slot_width = arc_disk_width(
                    label_reach_lines,
                    lf.family,
                    lf.size,
                    labels_style.offset,
                    float(raw_width),
                )
            else:
                slot_width = float(raw_width)
            # Non-sparse by construction: same key set as layout_charts.
            slot_height = ctx.layout_chart_heights[chart_id]
        elif chart.attached_table is not None:
            # Attached-table SVG path: wheel renders with height=None, so Vega
            # applies config.view.continuousHeight as the effective height.
            slot_width = chart.wheel_width
            slot_height = ctx.board_spec.style.chart_defaults.view.continuous_height
        else:
            continue

        theta_field = chart.theta
        theta_sum = sum(
            float(row[theta_field])
            for row in ctx.chart_results[chart_id]
            if theta_field in row
            and isinstance(row[theta_field], int | float | Decimal)
            and not isinstance(row[theta_field], bool)
        )

        # A donut always resolves a format (the integer preset by default).
        fmt = chart.style.total_style.value.format
        assert fmt is not None
        formatted = format_value(theta_sum, fmt)

        font = chart.style.total_style.value.font
        # font.size is guaranteed non-None by the theme cascade (_base.yaml:754-758).
        assert font.size is not None
        text_width = get_font_measurer(font.family).measure(formatted, font.size)

        hole_diameter = 2 * pie_hole_radius_px(
            min(slot_width, slot_height),
            chart.outer_fraction,
            chart.style.inner_radius,
        )
        if text_width <= hole_diameter:
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS,
                chart=chart_id,
                path=f"charts.{chart_id}.style.total.value.format",
                field="format",
                message=WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.message_template.format(
                    chart_id=chart_id,
                    formatted_value=formatted,
                    text_width=text_width,
                    hole_diameter=hole_diameter,
                    slot_width=slot_width,
                    slot_height=slot_height,
                ),
                fix=WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.fix_template,
            )
        )

    return warnings
