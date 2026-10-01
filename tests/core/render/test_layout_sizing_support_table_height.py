"""Regression tests: support_table strip height, positioning, and dx computation.

Covers:
  - _align_cols_heights must not inflate height for support_table charts.
  - Strip top must reserve vertical space for two-line x-axis labels.
  - _compute_support_table_entry_dx correctness: has_time_unit gating, numeric
    formatting, aggregate format field, empty-data → None sentinel.

Charts with support_table switch to autosize:pad so the strip renders below the plot
without shrinking it. Under pad mode, spec.height is the *inner* plot rect — Vega-Lite
adds strip height + axis padding on top of that, making the outer SVG taller than
target_height. _align_cols_heights must compensate by passing a corrected height
(target_height - overhead) so the outer SVG fits within the allocated slot.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)

_BOARD_STYLE = resolve_style(get_theme_style())
_CHART_CTX = resolve_chart_style_context(get_theme_style())


def _entry_dx(
    dt, data, dt_style, numerals=None, x_field=None, color_field=None, **kwargs
):
    """Call the dx helper the way the renderer does.

    The renderer resolves each entry's values and numerals once and passes both
    in, so these tests do the same rather than letting the helper re-derive
    them. ``numerals=None`` means no row declares a unit — the common strip, and
    the one whose cells are measured with their affixes exactly as before.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        _compute_support_table_entry_dx,
        _entry_values,
        plain_numerals,
    )

    values = _entry_values(dt, data, x_field, color_field)
    return _compute_support_table_entry_dx(
        dt,
        dt_style,
        entry_numerals=numerals
        if numerals
        else plain_numerals(dt, None, "Inter", [[]] * len(dt.entries)),
        values_per_entry=values,
        **kwargs,
    )


def test_align_cols_heights_corrects_height_for_support_table_chart():
    """_align_cols_heights must detect and correct height inflation for support_table charts.

    When autosize:pad is active (set by attach_support_table), passing target_height as
    spec.height produces an outer SVG = target_height + strip_overhead, inflating the
    cached height and defeating cols alignment.

    The fix: after re-rendering at target_height, measure the actual outer height.
    If it exceeds target_height (inflation detected), compute overhead and re-render
    with height = target_height - overhead so the outer SVG fits target_height.

    Pre-fix: _align_cols_heights caches the inflated height (target_height + overhead)
             in render_cache.
    Post-fix: _align_cols_heights caches a height <= target_height + small tolerance.
    """
    import importlib
    from unittest.mock import MagicMock, patch

    from dbt_charts.core.compile.models.board.normalized import LayoutItem
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    ITEM_WIDTH = 566.0
    TARGET_HEIGHT = 600.0
    STRIP_OVERHEAD = 80.0  # simulated autosize:pad height overhead

    # dt_bar is the shorter chart (has support_table; needs alignment re-render)
    dt_bar = BarChart(
        id="dt_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        style=None,
    )
    # Attach a minimal support_table so item.chart.support_table is not None
    dt_bar = dt_bar.model_copy(update={"support_table": [{"source": "revenue"}]})

    tall_bar = BarChart(
        id="tall_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        style=None,
    )

    item_dt = LayoutItem(type="chart", width=ITEM_WIDTH, height=0.0, chart=dt_bar)
    item_tall = LayoutItem(type="chart", width=ITEM_WIDTH, height=0.0, chart=tall_bar)

    executor = MagicMock()
    resolved = resolve_style(get_theme_style())

    # Simulate: dt_bar was initially rendered at 300px (shorter than tall_bar's 600px).
    # The first call at target_height inflates to target_height + STRIP_OVERHEAD.
    # The second call (corrected) fits within target_height.
    dt_render_count = [0]

    mock_dt = MagicMock(spec=ResolvedChart)
    mock_dt.id = "dt_bar"
    mock_dt.layout_padding = _ZERO_PADDING
    mock_tall = MagicMock(spec=ResolvedChart)
    mock_tall.id = "tall_bar"
    mock_tall.layout_padding = _ZERO_PADDING

    def fake_render(resolved, executor_, variables_, width, *, height=None, **kwargs):
        if resolved.id == "dt_bar":
            dt_render_count[0] += 1
            if height is not None and height >= TARGET_HEIGHT:
                # First alignment call: autosize:pad inflates by STRIP_OVERHEAD
                inflated = TARGET_HEIGHT + STRIP_OVERHEAD
                return (
                    f'<svg viewBox="0 0 {width} {inflated}"></svg>',
                    width,
                    inflated,
                    None,
                )
            else:
                # Second call with corrected height: fits within TARGET_HEIGHT
                corrected_h = height if height is not None else TARGET_HEIGHT
                return (
                    f'<svg viewBox="0 0 {width} {corrected_h}"></svg>',
                    width,
                    corrected_h,
                    None,
                )
        # tall_bar: not re-rendered (it is the tallest)
        return (
            f'<svg viewBox="0 0 {width} {TARGET_HEIGHT}"></svg>',
            width,
            TARGET_HEIGHT,
            None,
        )

    layout_sizing_mod = importlib.import_module("dbt_charts.core.render.layout_sizing")
    with patch.object(
        layout_sizing_mod, "_render_chart_to_svg", side_effect=fake_render
    ):
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_cols_heights,
        )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=resolved,
            chart_style_context=_CHART_CTX,
            render_cache={
                ("dt_bar", ITEM_WIDTH, 300.0): (
                    f'<svg viewBox="0 0 {ITEM_WIDTH} 300"></svg>',
                    300.0,
                ),
                ("tall_bar", ITEM_WIDTH, TARGET_HEIGHT): (
                    f'<svg viewBox="0 0 {ITEM_WIDTH} {TARGET_HEIGHT}"></svg>',
                    TARGET_HEIGHT,
                ),
            },
            natural_heights={
                ("dt_bar", ITEM_WIDTH): 300.0,
                ("tall_bar", ITEM_WIDTH): TARGET_HEIGHT,
            },
            pre_resolved={"dt_bar": mock_dt, "tall_bar": mock_tall},
            support_table_corrected_widths={},
        )

        _align_cols_heights([item_dt, item_tall], TARGET_HEIGHT, render_ctx)

    # After alignment, dt_bar's cached height must not exceed target_height + small tolerance.
    _, cached_height = render_ctx.render_cache[
        ("dt_bar", ITEM_WIDTH, TARGET_HEIGHT, NO_TITLE_SHIFT)
    ]

    assert cached_height <= TARGET_HEIGHT + 5, (
        f"_align_cols_heights cached inflated height {cached_height:.1f}px for a "
        f"support_table chart (target_height={TARGET_HEIGHT:.1f}px, tolerance=5px). "
        f"Under autosize:pad, spec.height is the inner plot rect — Vega adds "
        f"strip/axis overhead on top, so the outer SVG grows to "
        f"target_height + {STRIP_OVERHEAD:.0f}px. "
        f"Detect the inflation and re-render with height = target_height - overhead."
    )
    assert dt_render_count[0] == 2, (
        f"expected exactly 2 renders for dt_bar (initial alignment + correction); "
        f"got {dt_render_count[0]}"
    )


def test_strip_top_reserves_two_line_axis_label_space():
    """Strip top y must clear 2 axis label lines even when only one is active.

    With label_max_lines=2 (the universal default), the strip's first
    pixel y must be at least:
        spec_height + padding_top + axis_label_padding + 2 × axis_label_font_size

    That keeps two-line month labels (e.g. "Jan" over "2024", produced by the
    yearmonth labelExpr) clear of the strip top edge.

    Pre-fix: axis_offset counts only 1 line → strip_top = 325 < 336.
    Post-fix: label_max_lines=2 → strip_top = 336 >= 336.

    This applies only to position:bottom (axis gap is between plot and strip).
    For position:top, the axis is below the plot and no gap reservation is needed.
    """
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.support_table import axis_offset
    from dbt_charts.core.render.chart.support_table_attachment import _divider_y_pixel

    cs = resolve_chart_style_context(get_theme_style())
    # Use position=bottom explicitly: this test covers the axis-clearance guarantee
    # that only applies when the strip sits below the plot.
    style = get_theme_style().charts.support_table.model_copy(
        update={"position": "bottom"}
    )
    spec_height = 300.0

    top_y = _divider_y_pixel(
        style,
        axis_offset(cs, style, x_label_authored=False, chart_type=""),
        spec_height,
    )

    axis_x = resolved_axis_style(
        cs, "axis_x", "band", chart_type="", label_authored=False
    )
    label_padding = axis_x.labels.padding or 0.0
    label_font_size = axis_x.labels.font.size or 0.0
    assert label_font_size > 0, (
        "default theme must have a positive x-axis label font size"
    )

    two_line_clearance = (
        spec_height + style.padding_top + label_padding + 2 * label_font_size
    )
    assert top_y >= two_line_clearance, (
        f"strip top {top_y:.1f}px does not clear two axis-label lines; "
        f"need >= {two_line_clearance:.1f}px "
        f"(spec_height={spec_height:.0f} + padding_top={style.padding_top:.0f} + "
        f"label_padding={label_padding:.0f} + 2×font_size={label_font_size:.0f}). "
        f"Check that label_max_lines defaults to 2 in stark.yaml "
        f"and that axis_offset multiplies label font size by that count."
    )


# =============================================================================
# _compute_support_table_entry_dx unit tests
# =============================================================================


def test_compute_entry_dx_centers_banded_temporal_bar():
    # A temporal+timeUnit bar is a band scale (strip anchors at bandPosition:0.5),
    # so it centers the number column on the midpoint via dx exactly like an
    # ordinal bar. (Continuous axes — temporal without timeUnit, quantitative —
    # still return None; covered by test_compute_entry_dx_returns_none_for_continuous_x.)
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_style = get_theme_style().charts.support_table
    data = [{"revenue": 100.0}, {"revenue": 200.0}]

    result = _entry_dx(dt, data, dt_style, has_time_unit=True, x_type="temporal")
    assert result is not None and len(result) == 1 and result[0] > 0.0


def test_compute_entry_dx_returns_none_for_continuous_x():
    # Continuous axes have no bands: temporal without timeUnit and quantitative
    # both return None — a non-zero dx would shift text off the data point.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_style = get_theme_style().charts.support_table
    data = [{"revenue": 100.0}, {"revenue": 200.0}]

    for x_type in ("temporal", "quantitative"):
        result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type=x_type)
        assert result is None, f"{x_type} (continuous) must return None"


def test_compute_entry_dx_returns_half_max_width_for_numeric_source():
    # Basic case: numeric data, no format → dx = max_width / 2.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )
    from dbt_charts.core.font_measure import get_font_measurer

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    data = [{"revenue": 1.0}, {"revenue": 1000.0}]

    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="ordinal")
    assert result is not None and len(result) == 1

    font_size = dt_style.font.size
    assert font_size is not None
    measurer = get_font_measurer(dt_style.font.family, numeric=True)
    expected_max_w = max(
        measurer.measure("1.0", font_size), measurer.measure("1000.0", font_size)
    )
    assert abs(result[0] - expected_max_w / 2.0) < 0.5, (
        f"dx must equal max_formatted_width/2; got {result[0]:.2f}, "
        f"expected {expected_max_w / 2.0:.2f}"
    )


def test_compute_entry_dx_returns_none_for_empty_data():
    # No rows → all max_w = 0 → returns None (no centering needed).
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_style = get_theme_style().charts.support_table

    result = _entry_dx(dt, [], dt_style, has_time_unit=False, x_type="ordinal")
    assert result is None, "empty data must return None — no widths to center on"


def test_compute_entry_dx_uses_aggregate_entry_format():
    # ChartSupportTableAggregate also has a .format field; dx computation must use it.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.format_utils import format_value

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {"entries": [{"aggregate": "sum", "source": "revenue", "format": "$,.0f"}]}
    )
    data = [{"revenue": 1234.0}, {"revenue": 5678.0}]

    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="ordinal")
    assert result is not None and len(result) == 1

    font_size = dt_style.font.size
    assert font_size is not None
    measurer = get_font_measurer(dt_style.font.family, numeric=True)
    expected_max_w = max(
        measurer.measure(format_value(1234.0, "$,.0f"), font_size),
        measurer.measure(format_value(5678.0, "$,.0f"), font_size),
    )
    assert abs(result[0] - expected_max_w / 2.0) < 0.5


def test_compute_entry_dx_aggregates_multi_row_per_x():
    # HIGH fix: for ChartSupportTableAggregate entries, _compute_support_table_entry_dx
    # must aggregate the data by x-field before measuring string widths.  Without
    # aggregation, the dx is measured from raw per-row values which are typically
    # much smaller than the aggregated total — the column then lands left of the
    # band center instead of centered.
    #
    # This test uses aggregate="sum" over two x-groups each with two rows.
    # Group "A": 250+250=500 → "$500"  (raw widest: "$250")
    # Group "B": 500+500=1000 → "$1,000"  (raw widest: "$500")
    # Widest raw value → "$500" (3 chars + $); widest aggregate → "$1,000" (5 chars + $).
    # "$500" and "$1,000" have different rendered widths in the monospace font,
    # so the test can assert that the result reflects the aggregate width.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.format_utils import format_value

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {
            "entries": [
                {
                    "aggregate": "sum",
                    "source": "revenue",
                    "format": "$,.0f",
                    "label": "Rev",
                }
            ]
        }
    )
    data = [
        {"month": "A", "revenue": 250.0},
        {"month": "A", "revenue": 250.0},
        {"month": "B", "revenue": 500.0},
        {"month": "B", "revenue": 500.0},
    ]

    result = _entry_dx(
        dt, data, dt_style, has_time_unit=False, x_field="month", x_type="ordinal"
    )
    assert result is not None and len(result) == 1

    font_size = dt_style.font.size
    assert font_size is not None
    measurer = get_font_measurer(dt_style.font.family, numeric=True)
    # dx must equal half the width of the widest *aggregated* value ("$1,000"),
    # not half the widest raw row value ("$500").
    agg_max_w = measurer.measure(format_value(1000.0, "$,.0f"), font_size)  # "$1,000"
    raw_max_w = measurer.measure(format_value(500.0, "$,.0f"), font_size)  # "$500"
    assert agg_max_w > raw_max_w, "sanity: '$1,000' must be wider than '$500'"
    assert result[0] > raw_max_w / 2.0, (
        "dx must exceed half the widest raw row value — aggregate values are wider"
    )
    assert abs(result[0] - agg_max_w / 2.0) < 0.5, (
        f"dx must equal half the width of the widest aggregated value; "
        f"got {result[0]:.2f}, expected {agg_max_w / 2.0:.2f}"
    )


def test_text_mark_dx_negated_for_left_align():
    # HIGH fix: with align="left", a positive dx shifts the left edge of each
    # cell to band_center + dx, placing the entire column to the right of the
    # bar instead of centering it.  The dx must be negated so the left edge
    # sits at band_center - max_w/2, which centers the column on the bar.
    # align="right" keeps dx positive (right edge at band_center + max_w/2).
    from dbt_charts.core.render.chart.support_table_attachment import _text_mark_props

    style = get_theme_style().charts.support_table

    assert _text_mark_props(style, "left", dx=10.0).get("dx") == -10.0, (
        "align=left must negate dx so the column is centered "
        "(left edge at band_center - max_w/2)"
    )
    assert _text_mark_props(style, "right", dx=10.0).get("dx") == 10.0, (
        "align=right must keep dx positive (right edge at band_center + max_w/2)"
    )


def test_compute_entry_dx_count_distinct_over_non_numeric_source():
    # HIGH fix: aggregate=count_distinct over a non-numeric source (e.g. country name)
    # must still produce a positive dx.  Previously, float("US") was silently swallowed
    # by contextlib.suppress, leaving groups empty, so _aggregate_by_x returned [],
    # dx became 0, and the helper returned None — Bug B left unfixed for this shape.
    # Fix: count/count_distinct must not coerce source values to float; instead count
    # rows / distinct raw values so non-numeric sources are first-class.

    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {
            "entries": [
                {
                    "aggregate": "count_distinct",
                    "source": "country",
                    "label": "Countries",
                }
            ]
        }
    )
    # Non-numeric source values — exactly the case that fails before the fix.
    data = [
        {"month": "Jan", "country": "US"},
        {"month": "Jan", "country": "CA"},
        {"month": "Feb", "country": "US"},
        {"month": "Feb", "country": "CA"},
        {"month": "Feb", "country": "MX"},
    ]

    result = _entry_dx(
        dt, data, dt_style, has_time_unit=False, x_field="month", x_type="ordinal"
    )
    # Jan has 2 distinct countries → "2"; Feb has 3 → "3".
    # Both have positive rendered width so dx must be non-None and positive.
    assert result is not None, (
        "count_distinct over non-numeric source must produce a positive dx, "
        "not None (which would leave Bug B unfixed for this authoring shape)"
    )
    assert result[0] > 0.0, f"dx must be positive, got {result[0]}"


def test_compute_entry_dx_returns_none_for_quantitative_x():
    # MEDIUM fix: dx is only meaningful for band-scale x axes (ordinal/nominal).
    # For quantitative (continuous) x, there are no bands — applying dx would shift
    # the text right of the data point instead of centering it on a band.
    # _compute_support_table_entry_dx must return None when x_type is "quantitative".
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "revenue", "label": "Rev"}]}
    )
    data = [{"score": 1.0, "revenue": 100.0}, {"score": 2.0, "revenue": 200.0}]

    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="quantitative")
    assert result is None, (
        "quantitative x has no bands — dx would shift text off the data point; "
        f"expected None, got {result}"
    )


def test_compute_entry_dx_returns_none_for_temporal_without_time_unit():
    # MEDIUM fix: temporal x without timeUnit is a continuous scale — no bands.
    # Applying dx would shift the label away from the data point.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "revenue", "label": "Rev"}]}
    )
    data = [{"date": "2024-01-01", "revenue": 100.0}]

    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="temporal")
    assert result is None, (
        "temporal x without timeUnit is continuous — dx would shift text off the data point; "
        f"expected None, got {result}"
    )


def test_compute_entry_dx_font_size_none_raises():
    # HIGH fix: font_size=None is a theme misconfiguration, not a valid "no-op" state.
    # Silently returning None re-introduces Bug B for that theme without any signal.
    # The function must fail loudly (AssertionError) so the broken theme surfaces
    # immediately rather than silently shipping misaligned cells.
    import pytest

    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table.model_copy(
        update={
            "font": get_theme_style(
                get_default_theme_name()
            ).charts.support_table.font.model_copy(update={"size": None})
        }
    )
    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "revenue", "label": "Rev"}]}
    )
    data = [{"month": "Jan", "revenue": 100.0}]

    with pytest.raises(ValueError, match="theme cascade"):
        _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="ordinal")


def test_lex_sortable_ordinal_x_gets_dx_centering():
    # HIGH fix: apply_chart_support_table_post_pass reclassifies lex-sortable ordinal axes
    # as "temporal" for the validator's sampling check.  Before the fix, this
    # mutated x_type was forwarded to _compute_support_table_entry_dx, which then
    # returned None (temporal → no dx), leaving the strip cells off-center even
    # though _shared_x_encoding correctly emitted bandPosition:0.5.
    #
    # The fix: use a separate validator_x_type for the sampling reclassification.
    # x_type (the actual spec encoding type) stays "ordinal" and centering applies.
    #
    # This test exercises the apply_chart_support_table_post_pass path end-to-end via
    # attach_support_table's entry_dx parameter to confirm the bug is fixed.
    # It verifies that a lex-sortable ordinal axis still receives a dx value.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "revenue", "format": "$,.0f", "label": "Rev"}]}
    )
    # Lex-sortable year-month strings — the shape that gets reclassified to
    # "temporal" for sampling purposes but the spec encoding type is "ordinal".
    data = [
        {"month": "2024-01", "revenue": 500.0},
        {"month": "2024-02", "revenue": 1000.0},
        {"month": "2024-03", "revenue": 750.0},
    ]

    # Pass x_type="ordinal" (the spec encoding type, not the reclassified type)
    # — this is what apply_chart_support_table_post_pass should pass after the fix.
    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="ordinal")
    assert result is not None, (
        "lex-sortable ordinal x must receive dx centering — the spec encoding "
        "type is ordinal (bandPosition:0.5 is emitted), so dx must be computed. "
        "The validator reclassification to temporal must NOT be forwarded to "
        "_compute_support_table_entry_dx."
    )
    assert result[0] > 0.0, f"dx must be positive, got {result[0]}"


def test_compute_entry_dx_bare_non_numeric_source_returns_none():
    # HIGH fix: a bare source: <non-numeric column> is a valid authoring shape —
    # Vega renders string values directly.  After removing contextlib.suppress from
    # the source path, float("US") raises ValueError.  The source-only branch must
    # tolerate non-numeric values (skip them for dx measurement; dx falls to 0 →
    # returns None) rather than crashing the renderer.
    #
    # This test ensures the render does NOT crash when source points to a string column.
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
    )

    dt_style = get_theme_style().charts.support_table
    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "label", "label": "Name"}]}
    )
    data = [
        {"month": "Jan", "label": "Alpha"},
        {"month": "Feb", "label": "Beta"},
    ]

    # Must not raise — string source values can't be measured as numbers, dx → 0.
    result = _entry_dx(dt, data, dt_style, has_time_unit=False, x_type="ordinal")
    # All widths are 0 (no numeric values) → helper returns None.
    assert result is None, (
        "bare non-numeric source must not raise; dx falls to 0 → returns None; "
        f"got {result!r}"
    )


def test_lex_sortable_ordinal_x_dx_appears_in_rendered_spec():
    # Regression test for the validator_x_type split (commit 025c7dbb4).
    #
    # apply_chart_support_table_post_pass reclassifies lex-sortable ordinal axes to
    # "temporal" for validate_support_table_against_data so it allows >5 distinct
    # x values.  Before the fix that variable was also forwarded to
    # _compute_support_table_entry_dx, which treats "temporal" as "no dx" and
    # returns None.  The text marks therefore had no dx offset, leaving cells
    # visually off-center.
    #
    # The fix keeps x_type (the actual spec encoding type) separate from
    # validator_x_type so the centering path still sees "ordinal" and emits dx.
    #
    # This test drives the full generate_vega_lite_spec pipeline (not just
    # _compute_support_table_entry_dx directly) so that reverting the
    # validator_x_type split — restoring the single-variable path — turns this
    # test red.
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    # Lex-sortable year-month strings — the shape that gets reclassified to
    # "temporal" for validator sampling but whose spec encoding.x.type is "ordinal".
    data = [
        {"month": "2024-01", "value": 500.0},
        {"month": "2024-02", "value": 1000.0},
        {"month": "2024-03", "value": 750.0},
    ]

    chart = BarChart.model_validate(
        {
            "id": "lex_sort_dt",
            "type": "bar",
            "x": "month",
            "y": "value",
            "support_table": {
                "entries": [{"source": "value", "format": "$,.0f", "label": "Val"}]
            },
        }
    )

    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_CHART_CTX,
        width=400.0,
        height=300.0,
    )

    # The spec must be a layered chart (support_table wraps bar in layer).
    layers = spec.get("layer", [])
    assert layers, "support_table chart must produce a layered spec"

    # Find text mark layers — all rows emitted by the strip.
    text_marks = [
        layer["mark"]
        for layer in layers
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    assert text_marks, "support_table strip must emit at least one text layer"

    # Every text mark for a numeric source with a format must carry a non-zero dx.
    # Before the validator_x_type split, dx is None (temporal → no centering) and
    # "dx" is absent from the mark.  After the fix it is present and non-zero.
    dx_values = [m.get("dx") for m in text_marks]
    assert any(dx is not None and dx != 0 for dx in dx_values), (
        "lex-sortable ordinal x must produce non-zero dx on text marks so cells "
        f"center on the band midpoint; mark dx values: {dx_values!r}.  "
        "Check that apply_chart_support_table_post_pass passes x_type (the spec encoding "
        "type) rather than validator_x_type to _compute_support_table_entry_dx."
    )


def test_entry_dx_measures_the_bare_cell_not_the_anchor():
    """dx sizes the lane to a bare cell so the anchor overhangs left.

    The anchor carries affixes no other cell does. Sizing the lane to it would
    shift every cell right to accommodate one; measuring bare keeps the right
    edges in a single lane and lets the wider anchor hang into the y-axis
    gutter, where there is room.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.render.chart.support_table_attachment import (
        StripAnchor,
        StripNumerals,
    )

    dt = ChartSupportTable.model_validate(
        {"entries": [{"source": "goal", "format": "$,.3s"}]}
    )
    dt_style = get_theme_style().charts.support_table
    data = [{"goal": 441_000_000.0}, {"goal": 516_000_000.0}]
    declaring = StripNumerals(
        divisor=1e6,
        digit_spec=",.0~f",
        anchor=StripAnchor.by_drawn_index(),
        prefix="$",
        suffix="mn",
        suffix_is_magnitude=True,
    )

    bare = _entry_dx(dt, data, dt_style, numerals=[declaring], has_time_unit=True)
    affixed = _entry_dx(dt, data, dt_style, has_time_unit=True)

    assert bare is not None and affixed is not None
    # "516" is narrower than "$516mn", so the declaring row's lane is tighter.
    assert bare[0] < affixed[0]


# =============================================================================
# title.offset vs padding.top for position:top support_table
# =============================================================================


def _make_bar_chart(*, title: str | None, with_support_table: bool):
    """Build a vertical bar Chart for the title-offset tests."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    payload: dict = {
        "id": "test_dt_title",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "style": {"orientation": "vertical"},
    }
    if title is not None:
        payload["title"] = title
    if with_support_table:
        payload["support_table"] = {"entries": [{"source": "revenue"}]}
    return TypeAdapter(Chart).validate_python(payload)


_DT_DATA = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Feb", "revenue": 200.0},
]


def test_titled_support_table_top_uses_title_offset_not_padding_top():
    """A titled chart with support_table position:top must use title.offset, not padding.top.

    Pre-fix: padding.top bumped by strip_h pushes the chart title down, causing
             misalignment with sibling charts in the same row.
    Post-fix: title.offset = theme_title_offset + strip_h so the theme's breathing
              room is preserved while the strip space is reserved via the offset.
              padding.top is card_pad only (no strip height added).
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.render.chart.support_table_attachment import (
        support_table_strip_height,
    )
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _make_bar_chart(title="Revenue by Month", with_support_table=True)
    spec = generate_vega_lite_spec(chart, _DT_DATA, width=400, height=300)

    # Compute expected strip height for a single-row top support_table.
    dt_obj = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    theme = get_theme_style()
    dt_style = theme.charts.support_table  # position: top by default
    expected_strip_h = support_table_strip_height(dt_obj, dt_style, None)
    assert expected_strip_h > 0, (
        "strip height must be positive for this test to be meaningful"
    )
    # The theme's title.position.offset is preserved alongside strip_h.
    theme_title_offset = theme.title.position.offset or 0.0
    expected_offset = theme_title_offset + expected_strip_h

    title_block = spec.get("title")
    assert isinstance(title_block, dict), (
        f"spec must carry a title block when chart.title is set; got {title_block!r}"
    )
    actual_offset = float(title_block.get("offset", 0))
    assert actual_offset == pytest.approx(expected_offset, abs=1.0), (
        f"title.offset must equal theme_offset + strip_h ({expected_offset:.1f}px = "
        f"{theme_title_offset:.1f} + {expected_strip_h:.1f}); got {actual_offset:.1f}px. "
        "Pre-fix: padding.top is bumped instead, title drifts down."
    )

    # padding.top must NOT be bumped by strip_h (title.offset replaces the bump).
    padding = spec.get("padding", {})
    pad_top = float(padding.get("top", 0))
    assert pad_top < expected_strip_h, (
        f"padding.top ({pad_top:.1f}px) must be less than strip_h ({expected_strip_h:.1f}px); "
        "the old padding-bump path must not run for titled charts. "
        "Post-fix: title.offset carries the strip space, padding.top stays at 0."
    )


def test_titleless_support_table_top_keeps_padding_bump():
    """A titleless chart with support_table position:top must still use padding.top.

    The padding.top bump is the correct mechanism when there is no title to protect
    from misalignment — this path must remain unchanged after the fix.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.render.chart.support_table_attachment import (
        support_table_strip_height,
    )
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _make_bar_chart(title=None, with_support_table=True)
    spec = generate_vega_lite_spec(chart, _DT_DATA, width=400, height=300)

    dt_obj = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_style = get_theme_style().charts.support_table
    expected_strip_h = support_table_strip_height(dt_obj, dt_style, None)

    # No title block should be present.
    assert spec.get("title") is None, "titleless chart must not carry spec['title']"

    # padding.top must equal strip_h (card_pad is zero in this standalone render path).
    padding = spec.get("padding", {})
    pad_top = float(padding.get("top", 0))
    assert pad_top == pytest.approx(expected_strip_h, abs=1.0), (
        f"titleless chart: padding.top must equal strip_h ({expected_strip_h:.1f}px); "
        f"got {pad_top:.1f}px. Titleless path must still use bump_padding_top."
    )


def test_titled_support_table_alignment_matches_plain_sibling(tmp_path):
    """Titled charts with position:top support_table must align titles with plain siblings.

    Pre-fix: bump_padding_top shifts the support_table chart's title down by strip_h,
             causing visible title misalignment in cols: rows.
    Post-fix: title.offset carries the strip space, so the title stays at the same
              absolute y as a plain chart sibling in the same row.

    This test renders a real cols: board through the actual pipeline (compile →
    resolve → render_board_svg) and asserts that the absolute SVG y-position of
    each chart's title differs by < 5px. The tolerance covers sub-pixel rounding
    from VL's layout math.

    Pre-fix baseline: difference = strip_h ≈ 30px (guard fails at 30px not < 5px).
    Verified mechanically: with bump_padding_top the outer mark group y for the
    support_table chart is ~30px higher than the plain chart, while the title's relative
    y is the same in both — giving an absolute y gap equal to strip_h. With
    title.offset the plot group shifts down by strip_h while the title's relative y
    compensates, keeping both absolute title y values identical (verified:
    dt_chart: outer_y=79, title_rel=-63, abs=16; plain: outer_y=44, title_rel=-28,
    abs=16 — difference 0px on this branch).
    """
    import re

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile as compile_board
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.boards import render_board_svg

    _YAML = """
title: Title Alignment Test
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100.0]
      - [Feb, 200.0]
charts:
  dt_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue With SupportTable
    style:
      orientation: vertical
    support_table:
      - source: revenue
  plain_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue Plain
    style:
      orientation: vertical
cols:
  - dt_chart
  - plain_chart
"""

    result = compile_board(_YAML)
    assert result.success and result.board is not None, result.errors

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    svg = render_board_svg(
        resolved, executor, variables, "#ffffff", render_cache=render_cache
    )

    def _chart_title_abs_y(chart_id: str) -> float:
        """Return the absolute y of a chart's title group in the board SVG.

        Each chart section is bounded by its data-authored-path marker. Within
        the section, the first translate(x, y) is the outer mark group (the plot
        rectangle's top-left corner). The role-title group's translate gives its
        y-offset relative to that container. Absolute y = outer_y + role_title_y.
        """
        # Find the start of this chart's section
        m = re.search(rf'data-authored-path="charts\.{re.escape(chart_id)}"', svg)
        assert m is not None, f"chart {chart_id!r} not found in board SVG"
        # Find the start of the NEXT chart section (or end of SVG) to bound the section
        next_chart = re.search(r'data-authored-path="charts\.[^"]+"', svg[m.end() :])
        end = m.end() + next_chart.start() if next_chart else len(svg)
        section = svg[m.start() : end]

        # First translate in the section = outer mark group position
        outer_t = re.search(r'transform="translate\(([^)]+)\)"', section)
        assert outer_t is not None, f"no outer translate for {chart_id!r}"
        outer_y = float(outer_t.group(1).split(",")[1])

        # role-title group's translate (immediately inside role-title)
        title_t = re.search(
            r'role-title[^<]*<g[^>]*transform="translate\(([^)]+)\)"',
            section,
            re.DOTALL,
        )
        assert title_t is not None, f"no role-title translate for {chart_id!r}"
        title_rel_y = float(title_t.group(1).split(",")[1])

        return outer_y + title_rel_y

    dt_title_y = _chart_title_abs_y("dt_chart")
    plain_title_y = _chart_title_abs_y("plain_chart")
    diff = abs(dt_title_y - plain_title_y)

    assert diff < 5.0, (
        f"support_table chart title is {diff:.1f}px out of alignment with its plain "
        f"sibling (dt_chart abs_y={dt_title_y:.1f}, plain_chart abs_y={plain_title_y:.1f}). "
        "Pre-fix: bump_padding_top shifts the support_table title down by strip_h (~30px). "
        "Post-fix: title.offset carries the strip space; both titles land at the same y."
    )


def test_titled_support_table_title_gap_matches_strip_height(tmp_path):
    """After calibration, the gap between title baseline and plot equals strip_h + theme_offset.

    apply_chart_support_table_post_pass sets title.offset = theme_offset + strip_h (probe).
    _measure_vl_title_plot_gap returns gap = -(role_title_group_y + text_y).
    The calibration sets corrected_offset = max(0, 2*probe - gap_probe), so
    the final gap equals probe = theme_offset + strip_h.

    Pre-fix:  gap = probe + VL_baseline (≈ 68px; diff from strip_h ≈ 38px).
    Post-fix: gap ≈ theme_offset + strip_h (diff < 5px).

    The measurement uses production's _measure_vl_title_plot_gap to avoid
    re-implementing the formula and inheriting any measurement bugs.
    """
    import re

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile as compile_board
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.boards import render_board_svg
    from dbt_charts.core.render.chart.support_table_attachment import (
        support_table_strip_height,
    )
    from dbt_charts.core.render.layout_sizing import _measure_vl_title_plot_gap

    _YAML = """
title: Gap Test
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100.0]
      - [Feb, 200.0]
charts:
  dt_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue With SupportTable
    style:
      orientation: vertical
    support_table:
      - source: revenue
cols:
  - dt_chart
"""
    result = compile_board(_YAML)
    assert result.success and result.board is not None, result.errors

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    svg = render_board_svg(
        resolved, executor, variables, "#ffffff", render_cache=render_cache
    )

    m = re.search(r'data-authored-path="charts\.dt_chart"', svg)
    assert m is not None, "dt_chart not found in board SVG"
    section = svg[m.start() :]

    # Use production's measurement function to avoid re-implementing the formula.
    gap = _measure_vl_title_plot_gap(section)
    assert gap is not None, (
        "role-title structure not found in dt_chart section — "
        "_measure_vl_title_plot_gap returned None"
    )

    theme = get_theme_style()
    dt_obj = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_style = theme.charts.support_table
    strip_h = support_table_strip_height(dt_obj, dt_style, None)
    theme_title_offset = theme.title.position.offset or 0.0
    # Calibration targets: gap = probe = theme_offset + strip_h.
    expected_gap = theme_title_offset + strip_h

    assert abs(gap - expected_gap) < 5.0, (
        f"After title-offset calibration, expected gap ≈ {expected_gap:.1f}px "
        f"(theme_offset={theme_title_offset:.1f} + strip_h={strip_h:.1f}); "
        f"got gap={gap:.1f}px (diff={abs(gap - expected_gap):.1f}px > 5px). "
        "Pre-fix: gap = VL_baseline + probe (overcounts by ~22px). "
        "Post-fix: calibration cancels baseline so gap = probe."
    )


def test_support_table_title_corrected_offset_clamp_returns_zero():
    """_support_table_title_corrected_offset returns 0 when gap_probe > 2*probe_title_offset.

    The clamp `max(0, 2*probe - gap_probe)` fires when the measured gap exceeds
    2*probe, meaning the VL baseline alone already exceeds the probe offset. A
    negative corrected_offset would pull the title INTO the strip; zero is the
    safe floor (title baseline at exactly the strip top, no descender overlap).

    This is live production behavior: it fires on charts where the effective
    title-group height is large (e.g. title+subtitle or oversized theme fonts).
    """
    from dbt_charts.core.render.layout_sizing import (
        _support_table_title_corrected_offset,
    )

    # SVG with role-title structure where gap = 65 (> 2*probe=60 → clamp fires).
    # group_y = -40, text_y = -25 → gap = -((-40) + (-25)) = 65.
    svg = (
        '<g class="mark-group role-title"><g transform="translate(0,-40)">'
        '<g class="mark-group role-title-text">'
        '<text transform="translate(100,-25)">Title</text>'
        "</g></g></g>"
    )
    result = _support_table_title_corrected_offset(svg, probe_title_offset=30.0)
    # corrected = max(0, 2*30 - 65) = max(0, -5) = 0; |0 - 30| = 30 > 1 → 0.0 returned.
    assert result == pytest.approx(0.0), (
        f"clamp must return 0.0 when gap_probe (65) > 2*probe (60); got {result!r}"
    )


def test_correct_support_table_height_two_pass_fires_when_title_calibration_undershoots():
    """_correct_support_table_height issues a second-pass render when title calibration undershoots.

    When needs_title_calibration is True and the first correction render returns
    corrected_actual that still differs from target by > 1px (because a smaller
    title.offset also shrinks the autosize:pad chrome), a second pass corrects the
    residual undershoot.

    Pre-fix: the `if needs_title_calibration and abs(...)` block was absent — no
    regression path. Post-fix: exactly 3 _render_chart_to_svg calls fire.
    """
    import importlib
    from unittest.mock import MagicMock, patch

    from dbt_charts.core.compile.models.board.normalized import LayoutItem
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    # SVG with role-title structure so _support_table_title_corrected_offset fires.
    # group_y = -50, text_y = 10 → gap = -((-50)+10) = 40. probe=30 → corrected=20.
    _PROBE_SVG = (
        '<g class="mark-group role-title"><g transform="translate(0,-50)">'
        '<g class="mark-group role-title-text">'
        '<text transform="translate(100,10)">Title</text>'
        "</g></g></g>"
    )

    TARGET = 300.0
    ACTUAL_FIRST = TARGET + 50.0  # First (alignment) render inflates by 50
    ACTUAL_CORRECTION = (
        TARGET - 8.0
    )  # Correction render undershoots → second-pass fires

    dt_obj = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    dt_bar = BarChart(
        id="two_pass_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        title="My Chart",
        style=None,
    )
    dt_bar = dt_bar.model_copy(update={"support_table": [{"source": "revenue"}]})
    item_dt = LayoutItem(type="chart", width=500.0, height=0.0, chart=dt_bar)

    executor = MagicMock()
    resolved_style = resolve_style(get_theme_style())

    mock_dt = MagicMock(spec=ResolvedChart)
    mock_dt.id = "two_pass_bar"
    mock_dt.layout_padding = _ZERO_PADDING
    mock_dt.support_table = dt_obj

    render_count = [0]

    def fake_render(
        resolved,
        executor_,
        variables_,
        width,
        *,
        height=None,
        title_offset_override=None,
        **kwargs,
    ):
        render_count[0] += 1
        if render_count[0] == 1:
            # Alignment render: return role-title SVG + probe_title_offset=30.0.
            return _PROBE_SVG, width, ACTUAL_FIRST, 30.0
        if render_count[0] == 2:
            # First correction render with title_offset_override: undershoots target.
            return (
                f'<svg viewBox="0 0 {width} {ACTUAL_CORRECTION}"></svg>',
                width,
                ACTUAL_CORRECTION,
                None,
            )
        # Second-pass correction: the final_h = corrected_height + (target - corrected_actual).
        final_h = height if height is not None else TARGET
        return f'<svg viewBox="0 0 {width} {final_h}"></svg>', width, final_h, None

    layout_mod = importlib.import_module("dbt_charts.core.render.layout_sizing")
    with patch.object(layout_mod, "_render_chart_to_svg", side_effect=fake_render):
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_cols_heights,
        )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=resolved_style,
            chart_style_context=_CHART_CTX,
            render_cache={
                ("two_pass_bar", 500.0, TARGET): (_PROBE_SVG, TARGET),
            },
            natural_heights={("two_pass_bar", 500.0): TARGET - 50.0},
            pre_resolved={"two_pass_bar": mock_dt},
            support_table_corrected_widths={},
        )

        _align_cols_heights([item_dt], TARGET, render_ctx)

    assert render_count[0] == 3, (
        f"Expected 3 _render_chart_to_svg calls (1 alignment + 2 correction passes); "
        f"got {render_count[0]}. "
        "The second-pass block must fire when title calibration undershoots target."
    )


def test_titled_support_table_cols_card_heights_equal(tmp_path):
    """Both cols: siblings have equal rendered SVG height after support_table calibration.

    A titled position:top support_table chart re-renders with a corrected
    title.offset.  That offset change also shifts the outer SVG height.
    The sizing pass must correct back to the row target so both siblings
    produce the same outer height.

    Pre-fix: the support_table chart overshot its target by ~strip_h, so the
    two SVGs differed by that amount.
    Post-fix: both land within 2px of each other.
    """
    import re

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.compile import compile as compile_board
    from dbt_charts.core.execute.adapters import build_adapter_registry
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.boards import render_board_svg

    _YAML = """
title: Height Equality Test
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100.0]
      - [Feb, 200.0]
charts:
  dt_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue With SupportTable
    style:
      orientation: vertical
    support_table:
      - source: revenue
  plain_chart:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue Plain
    style:
      orientation: vertical
cols:
  - dt_chart
  - plain_chart
"""

    result = compile_board(_YAML)
    assert result.success and result.board is not None, result.errors

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    svg = render_board_svg(
        resolved, executor, variables, "#ffffff", render_cache=render_cache
    )

    def _chart_svg_height(chart_id: str) -> float:
        """Return the rendered SVG height for a chart section in the board SVG."""
        m = re.search(rf'data-authored-path="charts\.{re.escape(chart_id)}"', svg)
        assert m is not None, f"chart {chart_id!r} not found in board SVG"
        next_chart = re.search(r'data-authored-path="charts\.[^"]+"', svg[m.end() :])
        end = m.end() + next_chart.start() if next_chart else len(svg)
        section = svg[m.start() : end]
        vb = re.search(r'viewBox="0 0 [0-9.]+ ([0-9.]+)"', section)
        assert vb is not None, f"no viewBox found in {chart_id!r} section"
        return float(vb.group(1))

    dt_h = _chart_svg_height("dt_chart")
    plain_h = _chart_svg_height("plain_chart")

    assert abs(dt_h - plain_h) < 2.0, (
        f"cols: row heights differ by {abs(dt_h - plain_h):.1f}px "
        f"(dt_chart={dt_h:.1f}px, plain_chart={plain_h:.1f}px). "
        "After support_table height correction, both siblings must match."
    )


def test_titled_support_table_correction_path_does_not_raise(tmp_path):
    """_correct_support_table_height succeeds even when corrected_height < strip_h.

    The old fit guard checked strip_h >= spec_height and fired during
    _correct_support_table_height's internal re-render at corrected_height.
    Under autosize:pad the strip sits OUTSIDE the plot rect (absorbed
    additively by autosize), so strip_h >= corrected_height does NOT
    indicate a failure — the chart still renders correctly. The guard
    was imported from analysis of autosize:fit (a different mode) and
    was geometrically wrong for the actual pad config.

    This test sets up a mock render context where the initial alignment render
    returns an inflated height that, when corrected, yields corrected_height
    below strip_h. The correction path must complete without raising.
    """
    import importlib
    from unittest.mock import MagicMock, patch

    from dbt_charts.core.compile.models.board.normalized import LayoutItem
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.support_table_attachment import (
        support_table_strip_height,
    )

    dt_style = get_theme_style().charts.support_table
    dt_obj = ChartSupportTable.model_validate({"entries": [{"source": "revenue"}]})
    strip_h = support_table_strip_height(dt_obj, dt_style, None)
    assert strip_h > 0

    # Target height small enough that corrected_height < strip_h.
    # overhead = actual_height - target. corrected_height = target - overhead.
    # We want corrected_height = target - (strip_h + 10) < strip_h.
    # Choose: target = 2 * strip_h - 5  →  overhead = strip_h + 10 (simulated)
    TARGET = 2 * strip_h - 5.0
    OVERHEAD = strip_h + 10.0  # simulated inflation from the first render
    ACTUAL_FIRST = TARGET + OVERHEAD  # initial render returns this

    dt_bar = BarChart(
        id="dt_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        title="My Chart",
        style=None,
    )
    dt_bar = dt_bar.model_copy(update={"support_table": [{"source": "revenue"}]})
    item_dt = LayoutItem(type="chart", width=500.0, height=0.0, chart=dt_bar)

    executor = MagicMock()
    resolved_style = resolve_style(get_theme_style())

    mock_dt = MagicMock(spec=ResolvedChart)
    mock_dt.id = "dt_bar"
    mock_dt.layout_padding = _ZERO_PADDING
    mock_dt.support_table = dt_obj

    render_count = [0]

    def fake_render(resolved, executor_, variables_, width, *, height=None, **kwargs):
        render_count[0] += 1
        if render_count[0] == 1:
            # First alignment render: inflates by overhead
            return (
                f'<svg viewBox="0 0 {width} {ACTUAL_FIRST}"></svg>',
                width,
                ACTUAL_FIRST,
                None,
            )
        # Correction re-render: corrected_height < strip_h — must not raise
        corrected_h = height if height is not None else TARGET
        return (
            f'<svg viewBox="0 0 {width} {corrected_h}"></svg>',
            width,
            corrected_h,
            None,
        )

    layout_mod = importlib.import_module("dbt_charts.core.render.layout_sizing")
    with patch.object(layout_mod, "_render_chart_to_svg", side_effect=fake_render):
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_cols_heights,
        )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=resolved_style,
            chart_style_context=_CHART_CTX,
            render_cache={
                ("dt_bar", 500.0, TARGET): (
                    f'<svg viewBox="0 0 500 {TARGET}"></svg>',
                    TARGET,
                ),
            },
            natural_heights={("dt_bar", 500.0): TARGET - 1.0},
            pre_resolved={"dt_bar": mock_dt},
            support_table_corrected_widths={},
        )

        corrected_height = TARGET - OVERHEAD
        assert corrected_height < strip_h, (
            f"test precondition: corrected_height ({corrected_height:.1f}) "
            f"must be < strip_h ({strip_h:.1f}) to exercise the old guard path"
        )

        # Must not raise — the old guard would have fired here
        _align_cols_heights([item_dt], TARGET, render_ctx)

    assert render_count[0] == 2, (
        f"expected exactly 2 renders (initial + correction); got {render_count[0]}"
    )
