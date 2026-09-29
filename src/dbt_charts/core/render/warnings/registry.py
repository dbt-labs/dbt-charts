"""Warning detector registry.

DETECTORS is an explicit list of detector modules. Each module must expose:
  detect(ctx: WarningContext) -> list[Diagnostic]

Detector tasks append to DETECTORS as they land. Registered detectors run
sequentially; per-detector exceptions are isolated so a single bad detector
cannot abort the render pipeline.

Every detector is either GEOMETRY-dependent or DATA-dependent — pick a side
when you add one, and add it to the matching list below rather than a third
place:

  GEOMETRY-dependent: reads ``ctx.vega_specs`` or another WarningContext field
    a render-at-a-real-width pass populates (``table_overflows``,
    ``authored_chart_heights``, ``series_label_truncations``,
    ``text_truncations``, ``static_pagination_caps``,
    ``x_domain_paint_orders``). A chart that never
    laid out (an inactive tab, a collapsed `details:` section) has none of
    these — the detector's own ``if chart_id not in ctx.vega_specs`` /
    dict-membership guard is what keeps it silent for such a chart.
    ``renderer.py``'s ``_collect_render_warnings`` only builds ``vega_specs``
    for a chart with a real layout width, so this self-gating is sufficient;
    no per-detector width check is needed here.

  DATA-dependent: reads only ``ctx.chart_results`` / ``ctx.layer_results``
    (the queries' actual rows) and/or resolved chart config off
    ``ctx.board_spec``. The query executed and the result is what it is
    regardless of whether anything got painted — these must run for every
    chart whose query ran, on-screen or not. Both result maps are therefore
    built unconditionally in ``renderer.py``.
"""

from __future__ import annotations

import logging
from types import ModuleType

import dbt_charts.core.render.warnings.area_unstacked_reads_as_stacked as area_unstacked_reads_as_stacked
import dbt_charts.core.render.warnings.axis_label_collision as axis_label_collision
import dbt_charts.core.render.warnings.axis_title_truncated as axis_title_truncated
import dbt_charts.core.render.warnings.bar_band_width_too_narrow as bar_band_width_too_narrow
import dbt_charts.core.render.warnings.bar_grouped_series_coincide as bar_grouped_series_coincide
import dbt_charts.core.render.warnings.callout_text_truncated as callout_text_truncated
import dbt_charts.core.render.warnings.category_color_pin_unseen as category_color_pin_unseen
import dbt_charts.core.render.warnings.chart_title_truncated as chart_title_truncated
import dbt_charts.core.render.warnings.endpoint_label_gap_overflow as endpoint_label_gap_overflow
import dbt_charts.core.render.warnings.facet_panel_width_below_minimum as facet_panel_width_below_minimum
import dbt_charts.core.render.warnings.kpi_align_overflow as kpi_align_overflow
import dbt_charts.core.render.warnings.kpi_inline_fallback as kpi_inline_fallback
import dbt_charts.core.render.warnings.kpi_label_truncated as kpi_label_truncated
import dbt_charts.core.render.warnings.layer_x_domain_paint_order as layer_x_domain_paint_order
import dbt_charts.core.render.warnings.layered_chart_shared_y_axis_scale_mismatch as layered_chart_shared_y_axis_scale_mismatch
import dbt_charts.core.render.warnings.layout_min_exceeds_height as layout_min_exceeds_height
import dbt_charts.core.render.warnings.legend_position_width_fallback as legend_position_width_fallback
import dbt_charts.core.render.warnings.legend_values_unresolved as legend_values_unresolved
import dbt_charts.core.render.warnings.likely_currency_or_percent_missing_formatter as likely_currency_or_percent_missing_formatter
import dbt_charts.core.render.warnings.local_time_label_expr_on_bucketed_axis as local_time_label_expr_on_bucketed_axis
import dbt_charts.core.render.warnings.low_text_contrast as low_text_contrast
import dbt_charts.core.render.warnings.normalize_percent_format_reads_raw_value as normalize_percent_format_reads_raw_value
import dbt_charts.core.render.warnings.palette_unsupported as palette_unsupported
import dbt_charts.core.render.warnings.pie_dominant_segment as pie_dominant_segment
import dbt_charts.core.render.warnings.pie_too_many_segments as pie_too_many_segments
import dbt_charts.core.render.warnings.pie_total_exceeds_inner_radius as pie_total_exceeds_inner_radius
import dbt_charts.core.render.warnings.plot_height_below_minimum as plot_height_below_minimum
import dbt_charts.core.render.warnings.plot_width_below_minimum as plot_width_below_minimum
import dbt_charts.core.render.warnings.point_map_negative_size_values as point_map_negative_size_values
import dbt_charts.core.render.warnings.point_map_out_of_projection as point_map_out_of_projection
import dbt_charts.core.render.warnings.query_result_truncated as query_result_truncated
import dbt_charts.core.render.warnings.query_returned_zero_rows as query_returned_zero_rows
import dbt_charts.core.render.warnings.redundant_encoding as redundant_encoding
import dbt_charts.core.render.warnings.series_label_truncated as series_label_truncated
import dbt_charts.core.render.warnings.spark_label_truncated as spark_label_truncated
import dbt_charts.core.render.warnings.static_pagination_capped as static_pagination_capped
import dbt_charts.core.render.warnings.table_columns_overflow as table_columns_overflow
import dbt_charts.core.render.warnings.table_cramped as table_cramped
import dbt_charts.core.render.warnings.table_page_squeezed as table_page_squeezed
import dbt_charts.core.render.warnings.table_text_truncated as table_text_truncated
import dbt_charts.core.render.warnings.temporal_single_point as temporal_single_point
import dbt_charts.core.render.warnings.too_many_color_categories as too_many_color_categories
import dbt_charts.core.render.warnings.too_many_x_categories as too_many_x_categories
import dbt_charts.core.render.warnings.value_labels_crowd_width as value_labels_crowd_width
import dbt_charts.core.render.warnings.wide_measure_label_collision as wide_measure_label_collision
import dbt_charts.core.render.warnings.y_encoding_mostly_null as y_encoding_mostly_null
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext

logger = logging.getLogger(__name__)

# Reads ctx.vega_specs or another render-at-a-real-width WarningContext
# field; silent for a chart that never laid out (see module docstring).
_GEOMETRY_DETECTORS: list[ModuleType] = [
    axis_label_collision,
    axis_title_truncated,
    bar_band_width_too_narrow,
    bar_grouped_series_coincide,
    callout_text_truncated,
    chart_title_truncated,
    endpoint_label_gap_overflow,
    facet_panel_width_below_minimum,
    kpi_align_overflow,
    kpi_inline_fallback,
    kpi_label_truncated,
    layer_x_domain_paint_order,
    layout_min_exceeds_height,
    legend_position_width_fallback,
    low_text_contrast,
    pie_total_exceeds_inner_radius,
    plot_height_below_minimum,
    plot_width_below_minimum,
    series_label_truncated,
    spark_label_truncated,
    static_pagination_capped,
    table_columns_overflow,
    table_cramped,
    table_page_squeezed,
    table_text_truncated,
    temporal_single_point,
    too_many_color_categories,
    too_many_x_categories,
    value_labels_crowd_width,
]

# Reads only ctx.chart_results and/or resolved chart config; runs for every
# chart whose query executed, regardless of whether it is on-screen.
_DATA_DETECTORS: list[ModuleType] = [
    area_unstacked_reads_as_stacked,
    category_color_pin_unseen,
    layered_chart_shared_y_axis_scale_mismatch,
    legend_values_unresolved,
    likely_currency_or_percent_missing_formatter,
    local_time_label_expr_on_bucketed_axis,
    normalize_percent_format_reads_raw_value,
    palette_unsupported,
    pie_dominant_segment,
    pie_too_many_segments,
    point_map_negative_size_values,
    point_map_out_of_projection,
    query_result_truncated,
    query_returned_zero_rows,
    redundant_encoding,
    wide_measure_label_collision,
    y_encoding_mostly_null,
]

DETECTORS: list[ModuleType] = _GEOMETRY_DETECTORS + _DATA_DETECTORS


def run_all(ctx: WarningContext) -> list[Diagnostic]:
    """Run every registered detector and return the concatenated warnings.

    A detector that raises must not abort the render — exceptions are caught
    here, logged, and skipped.
    """
    results: list[Diagnostic] = []
    for detector in DETECTORS:
        try:
            results.extend(detector.detect(ctx))
        except Exception:  # noqa: BLE001 — detector failures must not break renders
            logger.exception("Warning detector %r raised; skipping", detector)
    # A detector that knows which authored key its complaint is about composes
    # its own `charts.<id>.<anchor>` path, so the squiggle lands on that field
    # rather than the whole chart block. The fallback below is for the
    # detectors that genuinely have nothing more specific to say — an
    # unreferenced chart, a table too wide for its slot — and it keeps a new
    # detector stampable with zero position wiring: stamping `.chart`, which it
    # already does, is enough.
    for d in results:
        if d.path is None and d.chart is not None:
            d.path = f"charts.{d.chart}"
    return results
