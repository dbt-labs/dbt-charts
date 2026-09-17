"""Tests for the BAR_BAND_WIDTH_TOO_NARROW render-warning detector.

Detection rule: fires on a bar chart when the estimated per-band pixel width
(the actual rendered spec extent / distinct category values) drops below the
readability floor — the scenario is daily-granularity bars where hundreds of
bands are squeezed into one chart's bounding dimension (width for a vertical
bar, height for horizontal) and the fill disappears under the bar's own
stroke, leaving "ghost bands".
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    PieChart,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_BAR_BAND_WIDTH_TOO_NARROW, Diagnostic
from dbt_charts.core.render.chart.emitters._cartesian import (
    min_height_for_horizontal_bar_categories,
)
from dbt_charts.core.render.chart.vl_field_maps import effective_bar_size
from dbt_charts.core.render.warnings import (
    WarningContext,
    bar_band_width_too_narrow as detector,
)

from ...core._board_utils import (
    _default_chart_style_context,
    make_test_resolved_board,
    make_test_resolved_chart,
)


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"day": f"2026-01-{i:02d}", "val": i} for i in range(n)]


def _grouped_rows(n_x: int, n_series: int) -> list[dict[str, Any]]:
    """n_x distinct x categories x n_series distinct series — a grouped bar's shape."""
    return [
        {"month": f"m{x}", "series": f"s{s}", "val": x + s}
        for x in range(n_x)
        for s in range(n_series)
    ]


def _make_ctx(
    chart: Any, rows: list[dict[str, Any]], x_type: str = "ordinal"
) -> WarningContext:
    from dbt_charts.core.compile.resolve import preferred_chart_width

    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    width = preferred_chart_width(chart, _default_chart_style_context())
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: {"encoding": {"x": {"type": x_type}}, "width": width}},
    )


def test_fires_on_daily_bars_in_narrow_chart() -> None:
    """~500 daily bars in a 580px chart -> ~1.16px/band, well under the floor."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val", width=580)
    warnings = detector.detect(_make_ctx(chart, _rows(500)))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert w.field == "day"
    # The grain of the x channel is what is wrong, so the mark belongs on `x:`
    # rather than on the whole chart block.
    assert w.path == "charts.c1.x"
    assert "500" in w.message
    assert "1.16px" in w.message
    assert w.fix is not None


def test_no_fire_on_normal_chart() -> None:
    """A typical 12-bar chart at the default 600px width is nowhere near the floor."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val")
    assert detector.detect(_make_ctx(chart, _rows(12))) == []


def test_no_fire_at_floor_boundary() -> None:
    """600px / 150 bands = 4.0px/band exactly at the floor -> does not fire."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val", width=600)
    assert detector.detect(_make_ctx(chart, _rows(150))) == []


def test_fires_just_below_floor_boundary() -> None:
    """600px / 151 bands = 3.97px/band, just under the floor -> fires."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val", width=600)
    warnings = detector.detect(_make_ctx(chart, _rows(151)))
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code


def _horizontal_ctx(
    chart: Any,
    rows: list[dict[str, Any]],
    *,
    layout_height: float | None,
    offset_field: str | None = None,
    offset_type: str = "nominal",
    facet_row_field: str | None = None,
) -> WarningContext:
    """Build a ctx matching what the real warning-detection render pass
    actually produces for a horizontal bar: the vega_specs entry carries the
    category/offset encoding (real production populates these; verified by
    ``test_bar_band_width_warning_fires_on_grouped_horizontal_bar`` in
    ``tests/core/render/test_renderer.py``) but NO top-level ``height`` key
    (the detection-pass render call never passes one -- see the module
    docstring). The real extent instead comes from ``layout_chart_heights``,
    set here directly rather than through the vega spec.

    ``layout_height=None`` omits the chart's entry from
    ``layout_chart_heights`` entirely.

    ``facet_row_field`` wraps the unit spec in a ``{"facet": {"row": ...}}``
    root -- a horizontal bar's category rides VL y, so ROW faceting (not
    column) is what can narrow it, the mirror of a vertical bar's category
    on VL x narrowing under COLUMN faceting (``_grouped_ctx``'s
    ``facet_field``).
    """
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    encoding: dict[str, Any] = {"y": {"type": "nominal"}}
    if offset_field is not None:
        encoding["yOffset"] = {"field": offset_field, "type": offset_type}
    unit = {"encoding": encoding, "width": 600}
    spec = (
        {"facet": {"row": {"field": facet_row_field}}, "spec": unit}
        if facet_row_field
        else unit
    )
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: spec},
        layout_chart_heights=(
            {} if layout_height is None else {resolved.id: layout_height}
        ),
    )


def test_no_fire_when_layout_height_is_unavailable() -> None:
    """A chart id absent from ctx.layout_chart_heights must not fire -- there
    is no real extent to judge, and this is a diagnostic pass, never the
    thing that raises on missing layout data.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="day",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    ctx = _horizontal_ctx(chart, _rows(500), layout_height=None)
    assert detector.detect(ctx) == []


def test_no_fire_on_comfortable_horizontal_bar() -> None:
    """Horizontal mirror of test_no_fire_on_normal_chart: 12 bars at a
    generous layout height is nowhere near the floor."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="day",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    ctx = _horizontal_ctx(chart, _rows(12), layout_height=600.0)
    assert detector.detect(ctx) == []


def test_no_fire_when_the_laid_out_height_clears_the_floor() -> None:
    """20 categories x 12 series: the emitter's own floor alone
    (min_height_for_horizontal_bar_categories(20, ...)) gives ~3.55px/bar
    and would fire, but a laid-out height comfortably taller than that
    floor means the real per-series band clears the readability floor.
    Dropping the laid-out-height operand (using the floor alone) would
    fire a false positive here.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    n_x, n_series = 20, 12
    rows = _grouped_rows(n_x=n_x, n_series=n_series)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)

    min_height = min_height_for_horizontal_bar_categories(
        n_x, resolved.style.axis_x, effective_bar_size(resolved.style.mark)
    )
    assert min_height / n_x / n_series < 4.0, (
        "test setup must actually cross the floor on the floor side, or "
        "this cannot discriminate the layout term from the floor term"
    )
    layout_height = min_height * 2
    assert layout_height / n_x / n_series >= 4.0, (
        "test setup must clear the floor once laid-out height is used, or "
        "this cannot discriminate the layout term from the floor term"
    )

    ctx = _horizontal_ctx(
        chart, rows, layout_height=layout_height, offset_field="series"
    )
    assert detector.detect(ctx) == []


def test_no_fire_when_a_layout_wrapper_height_clamp_undercounts_the_real_render() -> (
    None
):
    """A layout-wrapper `height:` can clamp `ctx.layout_chart_heights`
    below the floor (see the module docstring for the mechanism); this
    pins that a below-floor `layout_height` alone does not fire. Real
    end-to-end reproduction of this exact state:
    test_no_fire_on_layout_wrapper_clamped_single_series_horizontal_bar in
    tests/core/render/test_renderer.py.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="day",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    n = 200
    rows = _rows(n)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    min_height = min_height_for_horizontal_bar_categories(
        n, resolved.style.axis_x, effective_bar_size(resolved.style.mark)
    )
    layout_height = 300.0
    assert layout_height < min_height, (
        "test setup must feed a layout_height genuinely below the floor, "
        "or this cannot exercise the clamp state at all"
    )

    ctx = _horizontal_ctx(chart, rows, layout_height=layout_height)
    assert detector.detect(ctx) == []


def test_no_fire_on_temporal_x_encoding() -> None:
    """A dense temporal x-axis (hundreds of time points) is normal, not a defect.

    This pins the load-bearing _CATEGORICAL_TYPES guard: the detector must
    never fire when the Vega x-encoding type is 'temporal', even if the band
    count exceeds the floor on a narrow chart.
    """
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val", width=580)
    resolved = make_test_resolved_chart(chart, _rows(500))
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _rows(500)},
        vega_specs={resolved.id: {"encoding": {"x": {"type": "temporal"}}}},
    )
    assert detector.detect(ctx) == []


def test_fires_on_narrow_facet_panel() -> None:
    """Small multiples: the per-panel width lives on vega_specs[...]['spec']['width'],
    not the top level. The detector must read the panel width, not skip faceted charts.
    """
    chart = BarChart(id="c1", type="bar", query_name="q", x="day", y="val", width=580)
    resolved = make_test_resolved_chart(chart, _rows(500))
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _rows(500)},
        vega_specs={
            resolved.id: {
                "facet": {"column": {"field": "day"}},
                "spec": {"encoding": {"x": {"type": "ordinal"}}, "width": 100},
            }
        },
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code


def test_no_fire_on_non_bar_chart() -> None:
    chart = LineChart(id="c1", type="line", query_name="q", x="day", y="val")
    assert detector.detect(_make_ctx(chart, _rows(500))) == []


def test_no_fire_without_x() -> None:
    chart = PieChart(id="c1", type="pie", query_name="q", theta="val", color="day")
    assert detector.detect(_make_ctx(chart, _rows(500))) == []


def _grouped_ctx(
    chart: Any,
    rows: list[dict[str, Any]],
    width: float,
    *,
    offset_field: str | None = "series",
    offset_type: str = "nominal",
    facet_field: str | None = None,
) -> WarningContext:
    """Build a ctx whose vega_specs mirrors what render_resolved_chart would
    actually emit for a grouped (vertical) bar: an xOffset channel present
    only when the band is genuinely subdivided (offset_field is not None).

    Vertical only -- a horizontal bar's real extent comes from
    ``ctx.layout_chart_heights``, not a spec key (see the module docstring
    in ``bar_band_width_too_narrow.py``); its own fixture builder is
    ``_horizontal_ctx`` above.
    """
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    encoding: dict[str, Any] = {"x": {"type": "ordinal"}}
    if offset_field is not None:
        encoding["xOffset"] = {"field": offset_field, "type": offset_type}
    unit = {"encoding": encoding, "width": width}
    spec = (
        {"facet": {"column": {"field": facet_field}}, "spec": unit}
        if facet_field
        else unit
    )
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: spec},
    )


def test_fires_on_grouped_bar_subdivided_below_floor() -> None:
    """30 x-categories x 6 series, unauthored stack (defaults to grouped).

    600/30 = 20px per band clears the floor comfortably, but the emitted
    xOffset channel subdivides each band by series count: 20/6 = 3.33px per
    bar, under the floor. The detector must judge the bar, not the band.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = _grouped_rows(n_x=30, n_series=6)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.stack == "none"  # grouped is bar's unauthored default
    ctx = _grouped_ctx(chart, rows, width=600)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert "3.33px" in warnings[0].message
    assert "6 series" in warnings[0].message


def test_fires_on_grouped_bar_with_numeric_color_subdivided_below_floor() -> None:
    """Same shape as the string-color case above, but the grouping field is
    numeric — proven against a real emitted spec, not a hand-built one.

    A numeric/boolean color's ``xOffset`` must still emit a nominal/ordinal
    VL type (not ``quantitative``): this detector's own categorical guard
    treats a non-categorical offset the same as "no discrete grouping", so a
    quantitative offset would silently undercount the true series
    cardinality (6) as 1 and never fire this warning however narrow the real
    sub-bars are.
    """
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series_id",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = [
        {"month": f"m{x}", "series_id": s, "val": x + s}
        for x in range(30)
        for s in range(6)
    ]
    resolved = resolve(chart, rows, chart_style_context=_default_chart_style_context())
    vl = generate_vega_lite_spec(chart, rows, width=600.0)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: vl},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert "6 series" in warnings[0].message


def test_no_fire_on_stacked_control_same_data_and_width() -> None:
    """Same 30x6 shape and width, but stacked: the emitter never assigns an
    xOffset channel for a stacked bar, so no encoding key means no subdivision.

    600/30 = 20px per band, comfortably above the floor -> no warning. This is
    the sibling control proving the fix judges the emitted offset channel, not
    just series count in the data.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical", stack="zero"),
    )
    rows = _grouped_rows(n_x=30, n_series=6)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.stack == "zero"
    # A real stacked emitter never sets xOffset — mirror that (offset_field=None).
    ctx = _grouped_ctx(chart, rows, width=600, offset_field=None)
    assert detector.detect(ctx) == []


def test_fires_on_grouped_horizontal_bar_subdivided_below_floor() -> None:
    """Horizontal mirror of test_fires_on_grouped_bar_subdivided_below_floor.

    20 y-categories x 12 series, unauthored stack (grouped), with a
    deliberately tiny laid-out height (1px) so the emitter's own
    min_height_for_horizontal_bar_categories floor is what actually bounds
    the render (matching a chart the author never explicitly sized, or one
    squeezed by a layout-wrapper height clamp -- see the module docstring
    and test_no_fire_when_a_layout_wrapper_height_clamp_undercounts_the_real_render).
    That floor budgets room for ONE bar per category; it does not know
    about the yOffset sub-bands a color channel adds, so the per-series
    band still collapses under the readability floor. The expected pixel
    width is computed via the same production helper the detector itself
    calls, rather than pinned as a literal. Real end-to-end coverage
    (compile -> execute -> render):
    test_bar_band_width_warning_fires_on_grouped_horizontal_bar in
    tests/core/render/test_renderer.py.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    n_x, n_series = 20, 12
    rows = _grouped_rows(n_x=n_x, n_series=n_series)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.stack == "none"  # grouped is bar's unauthored default

    expected_min_height = min_height_for_horizontal_bar_categories(
        n_x, resolved.style.axis_x, effective_bar_size(resolved.style.mark)
    )
    expected_bar_width = expected_min_height / n_x / n_series
    assert expected_bar_width < 4.0, "test setup must actually cross the floor"

    ctx = _horizontal_ctx(chart, rows, layout_height=1.0, offset_field="series")
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert f"{expected_bar_width:.2f}px" in warnings[0].message
    assert "12 series" in warnings[0].message


def test_no_fire_on_stacked_horizontal_control_same_data_and_height() -> None:
    """Horizontal mirror of test_no_fire_on_stacked_control_same_data_and_width.

    Same 20x12 shape and laid-out height, but stacked: the emitter never
    assigns a yOffset channel for a stacked bar, so no encoding key means
    no subdivision -- the single-series case cannot cross the floor (the
    floor alone budgets a full bar per category, well above 4px, and
    series=1 means no further division).
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        style=BarChartStylePatch.model_construct(
            orientation="horizontal", stack="zero"
        ),
    )
    rows = _grouped_rows(n_x=20, n_series=12)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.stack == "zero"
    ctx = _horizontal_ctx(chart, rows, layout_height=1.0, offset_field=None)
    assert detector.detect(ctx) == []


def test_horizontal_bar_row_facet_divides_layout_height_across_panels() -> None:
    """Row-faceted horizontal bar: the laid-out ``layout_chart_heights``
    value is the WHOLE card's height, shared by every row panel, so
    ``_horizontal_render_extent`` must divide it by the row cardinality
    before judging a single panel's band -- the horizontal mirror of how
    ``_apply_facet_layout`` divides a vertical chart's spec-stamped width by
    the column cardinality.

    Numbers are picked so failing to divide gives a DIFFERENT, wrong verdict
    than dividing correctly: the emitter's own single-bar floor
    (``min_height_for_horizontal_bar_categories``) dominates the correctly
    halved height (700 / 2 = 350 < the floor) but not the undivided one
    (700 > the floor), so this discriminates "divides by row cardinality"
    from "forgot to", not just "runs without crashing".

    Faceted by ``region`` (not ``month``, the category field), matching
    ``test_fires_when_faceted_by_the_color_field``'s point: a field
    unrelated to the category channel does not narrow it, so every panel
    still paints the full 10-category domain and needs room for all of it.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
        multiples=MultiplesConfig(rows="region"),
    )
    rows = [
        {"month": f"m{x}", "series": f"s{s}", "region": r, "val": x + s}
        for x in range(10)
        for s in range(12)
        for r in ("north", "south")
    ]
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)

    min_height = min_height_for_horizontal_bar_categories(
        10, resolved.style.axis_x, effective_bar_size(resolved.style.mark)
    )
    layout_total = 700.0
    assert layout_total / 2 < min_height < layout_total, (
        "test setup must make the correctly halved height floor-dominated "
        "and the undivided height layout-dominated, or this test cannot "
        "tell a correct division from a missing one"
    )

    ctx = _horizontal_ctx(
        chart,
        rows,
        layout_height=layout_total,
        offset_field="series",
        facet_row_field="region",
    )
    expected_bar_width = min_height / 10 / 12
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert f"{expected_bar_width:.2f}px" in warnings[0].message
    assert "12 series" in warnings[0].message


def test_no_fire_on_full_overlap_grouped_bar() -> None:
    """overlap: full coincides the series into one visual column per x — the
    real emitter drops the xOffset channel entirely in this case, even though
    the chart is still 'grouped' (stack: none). Same 30x6 shape that fires
    without the override.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        width=600,
        style=BarChartStylePatch.model_construct(
            orientation="vertical", overlap="full"
        ),
    )
    rows = _grouped_rows(n_x=30, n_series=6)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.overlap == "full"
    # The real emitter pops the offset channel for overlap: full — mirror that.
    ctx = _grouped_ctx(chart, rows, width=600, offset_field=None)
    assert detector.detect(ctx) == []


def test_fires_when_faceted_by_the_color_field() -> None:
    """Small multiples partitioned on the same field bound to color.

    `multiples: {columns: series}` splits the *data* before it reaches a
    panel, but the color/offset scale is never one of the channels
    `facet_bound_position_channels` narrows — only a position channel (x/y)
    double-encoding the facet field resolves independently, and color stays
    shared across panels by design. So each panel's band still divides
    across the full, global series domain, with a single sub-slot occupied.
    Real render measurement: 1.18px bars at this exact shape. The detector
    must fire, not stay silent because the data looks partitioned.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        width=120,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
        multiples=MultiplesConfig(columns="series"),
    )
    rows = _grouped_rows(n_x=12, n_series=6)
    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.stack == "none"
    ctx = _grouped_ctx(chart, rows, width=120, facet_field="series")
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert "1.67px" in warnings[0].message
    assert "6 series" in warnings[0].message


def test_no_fire_when_faceted_by_the_x_field_itself() -> None:
    """Small multiples partitioned on the SAME field bound to x (not color) —
    the shape `facet_bound_position_channels` actually narrows.

    `multiples: {columns: cat}`, `x: cat` — the emitted spec carries
    `resolve.scale.x: "independent"` (each panel's x domain is genuinely one
    value; the columns-only-safe case). Whole-dataset `distinct` (24) would
    read a razor-thin band that isn't there; the real render is one
    full-width bar per panel. The detector must read the emitted `resolve`,
    not just the unpartitioned row count.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        width=42,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
        multiples=MultiplesConfig(columns="cat"),
    )
    rows = [{"cat": f"c{i:03d}", "val": i} for i in range(24)]
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={
            resolved.id: {
                "facet": {"column": {"field": "cat"}},
                "spec": {"encoding": {"x": {"type": "ordinal"}}, "width": 42},
                "resolve": {"scale": {"x": "independent"}},
            }
        },
    )
    assert detector.detect(ctx) == []


def test_no_fire_on_comfortable_grouped_bar() -> None:
    """12 x-categories x 3 series at 600px: 50px/band, 16.67px/bar — fine."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        color="series",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = _grouped_rows(n_x=12, n_series=3)
    ctx = _grouped_ctx(chart, rows, width=600)
    assert detector.detect(ctx) == []


def test_fires_on_wide_form_grouped_measures_below_floor() -> None:
    """y: [a, b, c] folds into a wide-form grouped bar — the emitter's own
    synthetic label field (never present in query rows) subdivides the band
    by len(chart.y), not by a color channel. 60 x-categories x 3 measures at
    600px: 10px/band, 3.33px/bar under the floor.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["a", "b", "c"],
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = [{"month": f"m{x}", "a": x, "b": x, "c": x} for x in range(60)]
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_LABEL_FIELD,
        WIDE_VALUE_FIELD,
    )

    resolved = make_test_resolved_chart(chart, rows)
    assert isinstance(resolved, ResolvedBarChart)
    # After normalization, y is narrowed to the synthetic value field and
    # wide_measures holds the original authored measure names.
    assert resolved.y == WIDE_VALUE_FIELD
    assert resolved.wide_measures == ("a", "b", "c")
    # Real emitter: xOffset field is the synthetic fold label, never in query rows.
    ctx = _grouped_ctx(chart, rows, width=600, offset_field=WIDE_LABEL_FIELD)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert "3.33px" in warnings[0].message
    assert "3 series" in warnings[0].message


def test_wide_form_grouped_by_dimension_counts_every_composite() -> None:
    """y: [a, b, c] + color: with two values subdivides the band six ways, not
    three. 30 x-categories at 600px: 20px/band; 6 composites → 3.33px/bar under
    the floor, where 3 measures alone (6.67px) would pass.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["a", "b", "c"],
        color="series",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = [
        {"month": f"m{x}", "series": f"s{k}", "a": x, "b": x, "c": x}
        for x in range(30)
        for k in range(2)
    ]
    from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_LABEL_FIELD

    ctx = _grouped_ctx(chart, rows, width=600, offset_field=WIDE_LABEL_FIELD)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert "3.33px" in warnings[0].message
    assert "6 series" in warnings[0].message


def test_no_fire_on_gradient_offset_type() -> None:
    """No real emitter builds a quantitative field-based offset any more
    (see ``test_fires_on_grouped_bar_with_numeric_color_subdivided_below_floor``),
    but the guard defends the shape directly on a hand-built context: a
    continuous offset isn't a discrete grouping, so the guard must treat
    'quantitative' the same as absent.
    """
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="val",
        width=600,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    rows = _grouped_rows(n_x=30, n_series=6)
    ctx = _grouped_ctx(
        chart, rows, width=600, offset_field="val", offset_type="quantitative"
    )
    assert detector.detect(ctx) == []


class TestDetectorReadsThePreEmitPanelWidth:
    """The panel width the bar emitter measures band-overlap against
    (``box.width``, set before the emitter runs) must be the exact same
    number this detector later reads off the stamped spec
    (``vega_specs[...]["spec"]["width"]``, set after emit by
    ``_apply_facet_layout``). If the two disagree, the emitter judges
    crowding against the full card width while the spec is stamped with the
    narrower panel width, so the detector's verdict would not be judging the
    width the emitter actually used. Proven against a real render of a real
    faceted chart, not a hand-built ``WarningContext``.
    """

    def test_emitter_box_width_matches_the_detectors_stamped_width(self) -> None:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.emitters import bar as bar_emitter
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        chart = BarChart(
            id="c1",
            type="bar",
            query_name="q",
            x="month",
            y="val",
            multiples=MultiplesConfig(columns="grp"),
        )
        rows = [
            {"grp": f"g{g}", "month": f"m{i:02d}", "val": i + 1}
            for g in range(3)
            for i in range(30)
        ]

        captured_box_widths: list[float] = []
        real_resolve_axis_x_overlap = bar_emitter.resolve_axis_x_overlap

        def _spy(*args: Any, **kwargs: Any) -> Any:
            captured_box_widths.append(kwargs["chart_width"])
            return real_resolve_axis_x_overlap(*args, **kwargs)

        import unittest.mock as mock_mod

        with mock_mod.patch.object(
            bar_emitter, "resolve_axis_x_overlap", side_effect=_spy
        ):
            vl = generate_vega_lite_spec(chart, rows, width=300.0)

        assert captured_box_widths, "resolve_axis_x_overlap was never called"
        emitter_panel_width = captured_box_widths[0]

        assert "facet" in vl
        stamped_panel_width = vl["spec"]["width"]
        assert emitter_panel_width == stamped_panel_width

        # The detector reads this exact dict path — run it against the real
        # emitted spec to confirm it sees the same number, not just that the
        # two producers agree in isolation.
        resolved = resolve(
            chart, rows, chart_style_context=_default_chart_style_context()
        )
        board = make_test_resolved_board(charts={resolved.id: resolved})
        ctx = WarningContext(
            board_spec=board,
            chart_results={resolved.id: rows},
            vega_specs={resolved.id: vl},
        )
        detector.detect(ctx)  # must not raise; exercises the real unwrap path
        assert vl["spec"]["width"] == emitter_panel_width


def test_widest_panel_count_not_a_flat_one_when_domain_subset_narrows() -> None:
    """Small multiples over a DIFFERENT field (`grp`, not x's own field
    `cat`) where each panel still holds a proper subset of the x domain —
    the general domain-subset case `facet_bound_position_channels` narrows
    now, not only the old name-matched degenerate shape where every panel
    held exactly one value by construction. Panel "A" holds 3 of 5
    categories, panel "B" holds 2. The detector must read the WIDEST
    panel's own count (3) — a flat 1 would compute 10/1=10px (comfortably
    above the floor, silent) and the whole-dataset union (5) would compute
    10/5=2px (narrower than what "A" actually paints). 10/3=3.33px is the
    real value, and it crosses the 4px floor."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        width=10,
        style=BarChartStylePatch.model_construct(orientation="vertical"),
        multiples=MultiplesConfig(columns="grp"),
    )
    rows = [{"cat": c, "grp": "A", "val": 1} for c in ("c0", "c1", "c2")] + [
        {"cat": c, "grp": "B", "val": 1} for c in ("c3", "c4")
    ]
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={
            resolved.id: {
                "facet": {"column": {"field": "grp"}},
                "spec": {"encoding": {"x": {"type": "ordinal"}}, "width": 10},
                "resolve": {"scale": {"x": "independent"}},
            }
        },
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert "3 bands" in warnings[0].message
    assert "3.33px" in warnings[0].message


# ── continuous (quantitative) x — bar-width-overlap variant ────────────────


def _numeric_hour_chart(**style_marks_bar: Any) -> BarChart:
    style = (
        BarChartStylePatch.model_validate({"marks": {"bar": style_marks_bar}})
        if style_marks_bar
        else None
    )
    return BarChart(id="c1", type="bar", query_name="q", x="hour", y="val", style=style)


def _numeric_rows(n: int) -> list[dict[str, Any]]:
    return [{"hour": h, "val": h + 1} for h in range(n)]


def _continuous_ctx(
    chart: BarChart, rows: list[dict[str, Any]], width: float
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={
            resolved.id: {"encoding": {"x": {"type": "quantitative"}}, "width": width}
        },
    )


def test_silent_for_histogram() -> None:
    """A histogram's x is quantitative but BINNED, so neither premise of the
    continuous ladder holds: `_emit_histogram` sizes the mark as a band
    fraction of the bin span and installs no `scale.padding`, and the bars
    drawn are bins, not rows. Running the continuous detector over the raw
    rows reports the raw-value spacing as a bar gap and the row count as a
    bar count -- every number in the message fictional. Same carve-out, and
    same reason, as `test_silent_for_histogram` in the bucketed-axis
    detector's suite. Must stay silent."""
    chart = BarChart(
        id="c1", type="histogram", query_name="q", x="hour", y="val", style=None
    )
    # Dense raw rows: adjacent spacing is a small fraction of a pixel, which
    # is exactly what the continuous branch would misreport as an overlap.
    rows = [{"hour": h / 25.0, "val": h} for h in range(300)]
    ctx = _continuous_ctx(chart, rows, width=600.0)
    assert detector.detect(ctx) == []


def test_fires_when_computed_min_size_clamp_forces_overlap() -> None:
    """10 hourly values (step 1) in a 100px chart, minus the emitter's own
    reserved padding (max_size/2 per side = 20 total, unauthored) ->
    (100 - 20) / 9 = 8.89px between adjacent values. A min_size floor above
    that (15px) forces the computed bar wider than the gap it has to sit in
    -> overlap."""
    chart = _numeric_hour_chart(gap=2.0, min_size=15.0, max_size=20.0)
    ctx = _continuous_ctx(chart, _numeric_rows(10), width=100.0)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code
    assert warnings[0].field == "hour"
    assert "bands" not in warnings[0].message, (
        "the continuous branch has no band scale — 'bands' is categorical-only wording"
    )
    assert "10 bars" in warnings[0].message
    assert "15.00px" in warnings[0].message
    assert "8.89px" in warnings[0].message


def test_does_not_fire_when_only_the_double_counted_padding_would_overlap() -> None:
    """Discriminates the correct padding correction from double-counting it.

    11 values (step 1) at 130px with an authored size of 11. The emitter
    reserves size/2 = 5.5px per side, so the usable span is 119px and the
    per-value step is 11.90px — an 11px bar fits, no warning. Subtracting
    2 * size instead gives (130 - 22) / 10 = 10.80px and would fire a false
    overlap that also prints a step narrower than the one actually rendered.
    """
    chart = _numeric_hour_chart(gap=2.0, min_size=4.0, max_size=20.0, size=11.0)
    ctx = _continuous_ctx(chart, _numeric_rows(11), width=130.0)
    assert detector.detect(ctx) == []


def test_no_fire_when_max_size_ceiling_keeps_bar_under_the_gap() -> None:
    """Same shape, a much wider chart: the computed default clamps at
    max_size well below the now-generous per-value gap -> no overlap."""
    chart = _numeric_hour_chart(gap=2.0, min_size=15.0, max_size=20.0)
    ctx = _continuous_ctx(chart, _numeric_rows(10), width=1000.0)
    assert detector.detect(ctx) == []


def test_fires_on_authored_size_too_wide_for_the_gap() -> None:
    """An authored bar.size bypasses the gap/min/max ladder entirely — it
    can still overlap, and this warns exactly the same way."""
    chart = _numeric_hour_chart(size=15.0)
    ctx = _continuous_ctx(chart, _numeric_rows(10), width=100.0)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].code == WARN_BAR_BAND_WIDTH_TOO_NARROW.code


def test_no_fire_on_comfortable_continuous_spacing() -> None:
    """24 hourly values in a generously wide chart, default theme sizing —
    the ordinary case must stay silent."""
    chart = _numeric_hour_chart()
    ctx = _continuous_ctx(chart, _numeric_rows(24), width=800.0)
    assert detector.detect(ctx) == []


def test_no_fire_on_single_distinct_x_value() -> None:
    """One distinct x value has no adjacent pair to measure a gap from."""
    chart = _numeric_hour_chart()
    ctx = _continuous_ctx(chart, [{"hour": 1, "val": 1}] * 5, width=100.0)
    assert detector.detect(ctx) == []


def test_fires_when_the_naive_estimate_would_silently_miss_the_overlap() -> None:
    """The old `render_width / domain_span` estimate ignores the pixel
    padding the emitter reserves on each side of the plot for the bar's own
    half-width (`padding = effective_bar_size / 2` per side, installed on
    every quantitative x — `bar.py`'s `_emit_vertical`) — it treats the whole
    render_width as usable domain span, so it systematically overestimates
    the real per-value pixel step and can miss an overlap the live scale
    would actually render.

    11 values 0..10 (step 1) at width=130, authored size=12.0: the naive
    estimate (130 / 10 = 13.0px) sits just above the 12px bar and would stay
    silent; the corrected estimate, which nets out `2 * effective_bar_size`
    (24px) before dividing ((130 - 24) / 10 = 10.6px), correctly falls below
    the 12px bar and fires.
    """
    chart = _numeric_hour_chart(size=12.0)
    ctx = _continuous_ctx(chart, _numeric_rows(11), width=130.0)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1, (
        f"the naive estimate (13.0px) sits above the 12px bar and would "
        f"silently miss this overlap; got {warnings!r}"
    )
