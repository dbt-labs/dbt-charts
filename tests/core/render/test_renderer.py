"""Tests for renderer.py's _collect_render_warnings width handling.

Regression coverage for the CONFIRMED case in
ai_notes/chart-chrome-vs-plot-dimensions-2026-07-20.md (#5): the warning-
detection pass rendered a throwaway spec with ``width=None``, which resolves
to the theme-family default width — never the chart's real, laid-out slot
width. A chart squeezed into a narrow column can render with visibly
unreadable bar bands while BAR_BAND_WIDTH_TOO_NARROW stays silent, because
the detector was checking against the wrong (too-wide) number.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render


def _daily_rows(n: int) -> list[dict[str, object]]:
    return [{"day": f"2026-01-{i:03d}", "val": i} for i in range(n)]


def _category_rows(n: int) -> list[dict[str, object]]:
    return [{"cat": f"c{i}", "val": i} for i in range(n)]


def _make_executor(board, query_registry, rows):
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


_BOARD_YAML = """
title: Narrow bar column
charts:
  narrow_chart:
    query: q
    type: bar
    x: day
    y: val
  filler:
    query: q
    type: bar
    x: day
    y: val
queries:
  q:
    sql: SELECT day, val FROM t
    source: test_source
cols:
  - id: narrow
    width: 150
    rows:
      - narrow_chart
  - filler
"""


def test_bar_band_width_warning_uses_real_layout_width_not_theme_default() -> None:
    """100 daily bars: 6px/band at the 600px theme default (no fire), 1.5px/band
    at the chart's real 150px column width (fires). The detector must see the
    real width — a chart this narrow ships genuinely unreadable ghost bands.
    """
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _daily_rows(100))

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" in codes, (
        "narrow_chart is laid out at ~150px for 100 daily bands (~1.5px/band, "
        "well under the 4px floor) — the detector must fire using the real "
        "layout width, not the unrelated theme-default width"
    )
    narrow_warning = next(
        w
        for w in render_result.warnings
        if w.code == "WARN-BAR-BAND-WIDTH-TOO-NARROW" and w.chart == "narrow_chart"
    )
    assert narrow_warning.chart == "narrow_chart"


_DONUT_NARROW_SLOT_YAML = """
title: Narrow donut with large total
charts:
  donut_chart:
    query: q
    type: donut
    theta: amount
    total:
      visible: true
      format: integer
    style:
      inner_radius: 0.6
      total:
        value:
          font:
            size: 18
queries:
  q:
    sql: SELECT amount FROM t
    source: test_source
rows:
  - height: 120
    cols:
      - width: 150
        rows:
          - donut_chart
      - filler:
          query: q
          type: donut
          theta: amount
"""


def test_pie_total_exceeds_inner_radius_uses_layout_height() -> None:
    """1,000,000 formatted as integer ('1,000,000' ~86px) overflows the hole
    (~64.8px) in a 150px-wide, 120px-tall slot (min(150,120)*0.9*0.6=64.8px).
    The detector must fire using the layout item height from
    ctx.layout_chart_heights (ResolvedLayoutItem.height), not from vega_specs.
    """
    result = compile(_DONUT_NARROW_SLOT_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(
        result.board, result.query_registry, [{"amount": 1_000_000}]
    )

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert "WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS" in codes, (
        "donut_chart has '1,000,000' (~86px) overflowing the hole in a "
        "150px-wide slot — the detector must fire reading height from "
        "layout_chart_heights (ResolvedLayoutItem.height), not vega_specs"
    )


_ATTACHED_TABLE_DONUT_YAML = _DONUT_NARROW_SLOT_YAML.replace(
    "Narrow donut with large total", "Attached-table donut with large total"
)

# Two data rows where one slice has < 2% share. That triggers hybrid (attached-
# table) mode in classify_arc_render_mode (invisible_slice_share default = 0.02).
# Total = 1,000,000 → "1,000,000" (~86px at 18px) overflows a 150px wheel hole
# (min(wheel_width, continuousHeight=300) × 0.9 × 0.6 ≤ 81px).
_ATTACHED_TABLE_ROWS = [{"amount": 999_990}, {"amount": 10}]


def test_pie_total_exceeds_inner_radius_fires_for_attached_table_donut() -> None:
    """Donuts with a tiny slice enter hybrid (attached-table) mode, render as SVG,
    and are absent from ctx.vega_specs. The detector must fire via chart.wheel_width
    and the theme's continuousHeight (the Vega effective height when wheel renders
    with height=None). The warning must identify donut_chart specifically."""
    result = compile(_ATTACHED_TABLE_DONUT_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _ATTACHED_TABLE_ROWS)

    render_result = render(result.board, executor, format="svg")

    # Pin the chart id — the board also contains a filler donut (auto-total).
    donut_warnings = [
        w
        for w in render_result.warnings
        if w.code == "WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS" and w.chart == "donut_chart"
    ]
    assert donut_warnings, (
        "donut_chart (hybrid/attached-table mode, absent from vega_specs) "
        "must warn via the wheel_width + continuousHeight path"
    )
    # Pin the branch: the attached-table path pairs wheel_width with the
    # theme's continuousHeight (300), so the message reports slot 150\u00d7300 —
    # the VL path would report the 120px layout height instead.
    assert "150\u00d7300px" in donut_warnings[0].message, donut_warnings[0].message


_SHARED_CHART_TWO_WIDTHS_YAML = """
title: Shared chart, no tabs
charts:
  s:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: vertical
  filler_a:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: vertical
  filler_b:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: vertical
queries:
  q:
    sql: SELECT cat, val FROM t
    source: test_source
rows:
  - cols: [s]
  - cols: [s, filler_a, filler_b]
"""


def test_bar_band_width_warning_uses_narrowest_placement_no_tabs() -> None:
    """A chart placed twice with no tabs involved at all: full-width in row 1,
    a 3-way column split (narrow) in row 2. 200 categories are unreadable at
    the narrow width but comfortably readable at the full width — the
    detector must fire, judged at the narrowest real placement. This is the
    same width-selection bug ``tabs`` scoping introduced (silencing a
    detector by picking the widest of two placements), reproduced on a board
    that never uses ``tabs`` at all.
    """
    result = compile(_SHARED_CHART_TWO_WIDTHS_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _category_rows(200))

    render_result = render(result.board, executor, format="svg")

    codes = {(w.chart, w.code) for w in render_result.warnings}
    assert ("s", "WARN-BAR-BAND-WIDTH-TOO-NARROW") in codes, (
        "chart 's' is unreadable at its narrow (3-column) placement — the "
        "detector must not be silenced by also appearing full-width "
        f"elsewhere on the same (non-tabbed) board: {render_result.warnings}"
    )


_PER_LAYER_QUERY_YAML = """
title: Migrated multi-metric tile
charts:
  tile:
    query: revenue_q
    type: bar
    x: month
    y: revenue
    layers:
      - type: line
        query: rate_q
        y: rate
queries:
  revenue_q:
    sql: SELECT month, revenue FROM t
    source: test_source
  rate_q:
    sql: SELECT month, rate FROM t
    source: test_source
rows:
  - tile
"""

_PER_LAYER_ROWS: dict[str, list[dict[str, object]]] = {
    "revenue_q": [
        {"month": "2026-01", "revenue": 5_000_000.0},
        {"month": "2026-02", "revenue": 4_800_000.0},
        {"month": "2026-03", "revenue": 5_200_000.0},
    ],
    "rate_q": [
        {"month": "2026-01", "rate": 0.45},
        {"month": "2026-02", "rate": 0.55},
        {"month": "2026-03", "rate": 0.50},
    ],
}


def _make_per_query_executor(board, query_registry, rows_by_query):
    """Executor whose adapter returns a different result set per query name."""

    def _execute(_query, _variables, **kwargs):
        ok = Mock()
        ok.is_success = True
        ok.data = rows_by_query[kwargs["query_name"]]
        ok.column_descriptions = None
        ok.resolved_relations = None
        ok.truncated_reason = None
        return ok

    mock_registry = Mock()
    mock_registry.execute.side_effect = _execute
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def test_y_scale_mismatch_fires_when_a_layer_has_its_own_query() -> None:
    """The shape the deterministic migrator emits: one query per chart layer.

    'revenue' (~5M) and 'rate' (~0.5) share one y-axis at a ~10,000,000x
    ratio, so the rate line is drawn as a flat zero. The detector reads the
    layer's own result set from ctx.layer_results — chart_results, keyed by
    chart id, only ever holds the base query's rows.
    """
    result = compile(_PER_LAYER_QUERY_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_per_query_executor(
        result.board, result.query_registry, _PER_LAYER_ROWS
    )

    render_result = render(result.board, executor, format="svg")

    mismatches = [
        w
        for w in render_result.warnings
        if w.code == "WARN-LAYERED-CHART-SHARED-Y-AXIS-SCALE-MISMATCH"
    ]
    assert mismatches, (
        "tile's layer draws 'rate' ([0, 1]) on the same axis as 'revenue' "
        f"(millions) — the detector must fire: {render_result.warnings}"
    )
    assert "revenue" in mismatches[0].message and "rate" in mismatches[0].message


_GROUPED_HORIZONTAL_YAML = """
title: Grouped horizontal bar
charts:
  grouped_chart:
    query: q
    type: bar
    style:
      orientation: horizontal
    x: month
    y: val
    color: series
queries:
  q:
    sql: SELECT month, series, val FROM t
    source: test_source
"""


def _grouped_series_rows(n_x: int, n_series: int) -> list[dict[str, object]]:
    return [
        {"month": f"m{x}", "series": f"s{s}", "val": x + s}
        for x in range(n_x)
        for s in range(n_series)
    ]


def test_bar_band_width_warning_fires_on_grouped_horizontal_bar() -> None:
    """A real compile -> execute -> render pass for a horizontal bar grouped
    by a 12-value color field: the emitter's own height floor
    (`min_height_for_horizontal_bar_categories`) only budgets room for one
    bar per category, never for the yOffset sub-bands a color channel adds,
    so the per-series band still collapses under the readability floor even
    though the whole render path (including the auto-grown height) ran for
    real.
    """
    result = compile(_GROUPED_HORIZONTAL_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(
        result.board, result.query_registry, _grouped_series_rows(20, 12)
    )

    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors

    narrow = [
        w
        for w in render_result.warnings
        if w.code == "WARN-BAR-BAND-WIDTH-TOO-NARROW" and w.chart == "grouped_chart"
    ]
    assert narrow, (
        "grouped_chart divides 20 categories x 12 series across a real, "
        "laid-out height, well under the 4px floor per sub-band, but the "
        f"detector stayed silent: {render_result.warnings}"
    )
    assert "12 series" in narrow[0].message


def test_no_fire_on_comfortable_grouped_horizontal_bar() -> None:
    """Same shape, only 2 series: comfortably above the floor, must stay silent."""
    result = compile(_GROUPED_HORIZONTAL_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(
        result.board, result.query_registry, _grouped_series_rows(20, 2)
    )

    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors

    codes = {w.code for w in render_result.warnings}
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" not in codes


_DENSE_SINGLE_SERIES_HORIZONTAL_YAML = """
title: Dense single-series horizontal bar
charts:
  dense_chart:
    query: q
    type: bar
    height: 80
    style:
      orientation: horizontal
    x: day
    y: val
queries:
  q:
    sql: SELECT day, val FROM t
    source: test_source
"""


def test_no_fire_on_dense_single_series_horizontal_bar() -> None:
    """500 daily categories in an 80px-tall authored slot: no color channel,
    so there is no yOffset sub-band to collapse. vega_lite.py's own
    min_height_for_horizontal_bar_categories floor grows the real rendered
    height well past the authored 80px, exactly the way it would for the
    live chart, so BAR_BAND_WIDTH_TOO_NARROW must stay silent here (a
    too-small authored height is WARN-PLOT-HEIGHT-BELOW-MINIMUM's concern,
    not this detector's).
    """
    result = compile(_DENSE_SINGLE_SERIES_HORIZONTAL_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _daily_rows(500))

    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors

    codes = {w.code for w in render_result.warnings}
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" not in codes


_LAYOUT_WRAPPER_CLAMPED_HORIZONTAL_YAML = """
title: Layout-wrapper clamped horizontal bar
rows:
  - height: 300
    rows:
      - dense_chart
charts:
  dense_chart:
    query: q
    type: bar
    style:
      orientation: horizontal
    x: day
    y: val
queries:
  q:
    sql: SELECT day, val FROM t
    source: test_source
"""


def test_no_fire_on_layout_wrapper_clamped_single_series_horizontal_bar() -> None:
    """200 daily categories, single series, wrapped in a `rows:` item with
    its OWN authored `height: 300` -- unlike a chart-root `height:` (see
    test_no_fire_on_dense_single_series_horizontal_bar), a layout-wrapper
    height is a ceiling `sizing.py`'s `_clamp_to_authored_ceiling` keeps
    even when the chart's real content resolves taller, so
    `ctx.layout_chart_heights` reads the clamped 300px here, not the
    ~7872px the 200 categories actually need. The chart still paints at
    ~7872px (WARN-LAYOUT-MIN-EXCEEDS-HEIGHT says so in the same render), so
    a detector that dropped the floor re-application would report a
    fabricated ~1.5px band width on bars that actually paint ~39px wide.
    """
    result = compile(_LAYOUT_WRAPPER_CLAMPED_HORIZONTAL_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _daily_rows(200))

    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors

    codes = {w.code for w in render_result.warnings}
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" not in codes, render_result.warnings
    assert "WARN-LAYOUT-MIN-EXCEEDS-HEIGHT" in codes


_GENEROUS_HEIGHT_GROUPED_HORIZONTAL_YAML = _GROUPED_HORIZONTAL_YAML.replace(
    "    type: bar\n    style:",
    "    type: bar\n    height: 1500\n    style:",
)


def test_no_fire_when_authored_height_clears_the_floor() -> None:
    """Same 20 categories x 12 series shape as
    test_bar_band_width_warning_fires_on_grouped_horizontal_bar, but with a
    generous authored height (1500px): the chart's real laid-out slot is
    tall enough that the per-series band clears the readability floor, so
    the detector must stay silent even though the same 12-series shape
    fires at the default height. Proves ctx.layout_chart_heights (not a
    fixed authored-shape assumption) is what the verdict actually tracks.
    """
    result = compile(_GENEROUS_HEIGHT_GROUPED_HORIZONTAL_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(
        result.board, result.query_registry, _grouped_series_rows(20, 12)
    )

    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors

    codes = {w.code for w in render_result.warnings}
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" not in codes
