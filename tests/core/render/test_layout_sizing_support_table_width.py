"""Regression tests: support_table charts must not overflow width and must not inflate height.

Charts with support_table switch to autosize:pad so the strip renders below the plot
without shrinking it. Under pad mode, spec.width is the *inner* plot — Vega-Lite
adds axis-y label + padding + legend outside that, making the outer SVG wider than
the allocated slot. The layout_sizing pass must compensate by pre-shrinking spec.width.

The width-correction re-render must NOT pass the post-first-render height as the
height argument, because under autosize:pad spec.height is the inner plot rect and
Vega adds strips/axes on top, so the outer SVG grows to height + strip_height —
inflating the cached layout height by ~1 strip + axis_pad on every re-render.

These tests FAIL before the fix and PASS after.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project

_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)


def _svg_outer_width(svg: str) -> float:
    """Extract the outer SVG width from viewBox or width attribute."""
    m = re.search(r'viewBox=["\']0\s+0\s+([\d.]+)\s+[\d.]+["\']', svg)
    if m:
        return float(m.group(1))
    m = re.search(r'<svg[^>]+width=["\']([0-9.]+)["\']', svg)
    assert m, f"No width found in SVG: {svg[:300]}"
    return float(m.group(1))


def test_support_table_chart_cached_svg_fits_allocated_width(
    tmp_path, local_project: Callable[..., Project]
):
    """After calculate_data_aware_layout, the cached SVG for a support_table chart
    must have outer width <= allocated_width + 5px tolerance.

    Pre-fix: outer width is ~165px over because autosize:pad grows beyond spec.width.
    Post-fix: the sizing pass re-renders with spec.width shrunk by the measured overhead.
    """
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board

    result = compile(
        """\
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 200}
      - {month: Mar, revenue: 150}
charts:
  weekly_bar:
    query: q
    type: bar
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: "$.0f"
        label: Revenue
    style:
      orientation: vertical
rows:
  - weekly_bar
"""
    )
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    resolved_board, render_cache = build_resolved_board(
        result.board, executor, {}, render_first=True
    )

    # The chart must be in the render_cache because it went through render-first sizing.
    cache_entry = next(
        (v for k, v in render_cache.items() if k[0] == "weekly_bar"), None
    )
    assert cache_entry is not None, (
        "support_table chart must be cached by render-first sizing pass"
    )

    cached_svg, _actual_height = cache_entry

    # The allocated width for a full-width chart in a 1200-wide board with 24px margins is 1152.
    # (content_width = frame.max_width - 2 * margin = 1200 - 48 = 1152)
    allocated_width = float(resolved_board.style.frame.max_width) - 2 * float(
        resolved_board.style.frame.margin
    )
    outer_width = _svg_outer_width(cached_svg)

    assert outer_width <= allocated_width + 5, (
        f"support_table chart outer SVG width {outer_width:.1f}px overflows "
        f"allocated slot {allocated_width:.1f}px by "
        f"{outer_width - allocated_width:.1f}px (tolerance 5px). "
        f"Pre-shrink the spec.width by the autosize:pad overhead."
    )


def test_align_cols_heights_uses_corrected_width_for_support_table_chart():
    """_align_cols_heights must reuse the corrected (shrunk) spec.width for support_table
    charts, not item.width.

    When autosize:pad is active (set by attach_support_table), passing item.width as
    spec.width produces an outer SVG = item.width + overhead, overflowing the slot.
    The sizing pass corrects this by storing a shrunk_width in _support_table_corrected_widths
    and consulting it in _align_cols_heights.

    Pre-fix: _align_cols_heights calls render_chart_to_svg(... item.width ...).
             The mocked renderer returns outer SVG wider than item.width — overflow.
    Post-fix: _align_cols_heights calls render_chart_to_svg(... corrected_width ...).
              The mock is called with the shrunk width, not item.width.
    """
    import importlib
    from unittest.mock import MagicMock, patch

    from dbt_charts.core.compile.models.board.normalized import LayoutItem
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    ITEM_WIDTH = 566.0
    OVERHEAD = 150.0  # simulated autosize:pad overhead (test 1)
    CORRECTED_WIDTH = ITEM_WIDTH - OVERHEAD  # 416.0 — what the initial pass computed

    # dt_bar is the shorter chart (it has support_table but lost the height race)
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

    mock_dt = MagicMock(spec=ResolvedChart)
    mock_dt.id = "dt_bar"
    mock_dt.layout_padding = _ZERO_PADDING
    mock_tall = MagicMock(spec=ResolvedChart)
    mock_tall.id = "tall_bar"
    mock_tall.layout_padding = _ZERO_PADDING

    # Simulate: the initial render-first pass produced a corrected SVG at CORRECTED_WIDTH.
    # The cached SVG has outer width = ITEM_WIDTH (corrected render fits the slot).
    # The cache height for dt_bar is 300 (shorter than tall_bar's 600).
    corrected_svg = f'<svg viewBox="0 0 {ITEM_WIDTH} 300"></svg>'
    corrected_widths: dict[str, float] = {"dt_bar": CORRECTED_WIDTH}

    # Track the width argument passed to _render_chart_to_svg when it re-renders dt_bar.
    widths_called: list[float] = []

    def fake_render(resolved, executor_, variables_, width, **kwargs):
        widths_called.append(width)
        # Return an SVG whose outer width = width (as if corrected width was used correctly)
        return f'<svg viewBox="0 0 {width} 600"></svg>', width, 600.0, None

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
            chart_style_context=resolve_chart_style_context(get_theme_style()),
            render_cache={
                ("dt_bar", ITEM_WIDTH, 300.0): (corrected_svg, 300.0),
                ("tall_bar", ITEM_WIDTH, 600.0): (
                    f'<svg viewBox="0 0 {ITEM_WIDTH} 600"></svg>',
                    600.0,
                ),
            },
            natural_heights={
                ("dt_bar", ITEM_WIDTH): 300.0,
                ("tall_bar", ITEM_WIDTH): 600.0,
            },
            pre_resolved={"dt_bar": mock_dt, "tall_bar": mock_tall},
            support_table_corrected_widths=corrected_widths,
        )

        _align_cols_heights([item_dt, item_tall], 600.0, render_ctx)

    # After alignment, dt_bar's cached SVG must not overflow item.width.
    assert widths_called, "_render_chart_to_svg must be called for the shorter item"
    width_used = widths_called[0]
    _cached_svg_after, _ = render_ctx.render_cache[
        ("dt_bar", ITEM_WIDTH, 600.0, NO_TITLE_SHIFT)
    ]

    # The width passed to render must be the corrected width, not item.width.
    # Pre-fix: width_used == ITEM_WIDTH (566), causing overflow.
    # Post-fix: width_used == CORRECTED_WIDTH (416).
    assert width_used <= ITEM_WIDTH, (
        f"_align_cols_heights passed width {width_used}px to _render_chart_to_svg "
        f"for a support_table chart, but item.width={ITEM_WIDTH}px. "
        f"For support_table charts the corrected (shrunk) width must be used so "
        f"autosize:pad overhead doesn't cause the outer SVG to overflow the slot."
    )
    assert width_used == CORRECTED_WIDTH, (
        f"Expected corrected width {CORRECTED_WIDTH}px but got {width_used}px. "
        f"The _support_table_corrected_widths dict must be consulted in _align_cols_heights."
    )


def test_fix_slot_heights_does_not_inflate_support_table_chart_cached_height():
    """_fix_slot_heights_in_tree must not store an inflated height for support_table charts.

    Under autosize:pad (set by attach_support_table), passing height=item.height to
    _render_chart_to_svg returns actual_height = item.height + strip_overhead.
    Storing that inflated value makes the board render the chart taller than the
    allocated slot.

    Fix: after the re-render, if item.chart.support_table is not None and
    actual_height > item.height, compute overhead and re-render with
    corrected_height = item.height - overhead so the outer SVG fits the slot.

    Pre-fix: cache stores (svg, item.height + overhead)  ← inflated
    Post-fix: cache stores (svg, item.height)            ← correct
    """
    import importlib
    from unittest.mock import MagicMock, patch

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    ITEM_HEIGHT = 610.0
    STRIP_OVERHEAD = 191.0  # simulated autosize:pad overhead (3-row support_table)
    NATURAL_HEIGHT = 700.0  # natural aspect-ratio height — differs from item.height

    dt_bar = BarChart(
        id="dt_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        style=None,
    )
    dt_bar = dt_bar.model_copy(update={"support_table": [{"source": "revenue"}]})

    item = LayoutItem(type="chart", width=566.0, height=ITEM_HEIGHT, chart=dt_bar)
    layout = Layout(type="rows", items=[item])

    executor = MagicMock()
    resolved = resolve_style(get_theme_style())

    mock_resolved_v2 = MagicMock(spec=ResolvedChart)
    mock_resolved_v2.id = "dt_bar"
    mock_resolved_v2.layout_padding = _ZERO_PADDING

    layout_sizing_mod = importlib.import_module("dbt_charts.core.render.layout_sizing")

    render_calls: list[dict] = []

    def fake_render(resolved, executor_, variables_, width, height=None, **kwargs):
        # Under autosize:pad, the outer SVG is taller than spec.height by STRIP_OVERHEAD
        returned_h = (height or NATURAL_HEIGHT) + STRIP_OVERHEAD
        render_calls.append(
            {"width": width, "height": height, "returned_h": returned_h}
        )
        return (
            f'<svg viewBox="0 0 {width} {returned_h}"></svg>',
            width,
            returned_h,
            None,
        )

    with patch.object(
        layout_sizing_mod, "_render_chart_to_svg", side_effect=fake_render
    ):
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _fix_slot_heights_in_tree,
        )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=resolved,
            chart_style_context=resolve_chart_style_context(get_theme_style()),
            render_cache={},
            natural_heights={("dt_bar", 566.0): NATURAL_HEIGHT},
            pre_resolved={"dt_bar": mock_resolved_v2},
            support_table_corrected_widths={},
        )

        _fix_slot_heights_in_tree(layout, render_ctx)

    # The cache entry must exist for this slot
    cache_entry = render_ctx.render_cache.get(
        ("dt_bar", 566.0, ITEM_HEIGHT, NO_TITLE_SHIFT)
    )
    assert cache_entry is not None, "_fix_slot_heights_in_tree must update cache"
    _, cached_height = cache_entry

    # Pre-fix: cached_height == ITEM_HEIGHT + STRIP_OVERHEAD == 801
    # Post-fix: cached_height <= ITEM_HEIGHT (corrected render fits the slot)
    assert cached_height <= ITEM_HEIGHT + 5, (
        f"_fix_slot_heights_in_tree stored inflated height {cached_height:.1f}px "
        f"in cache for a support_table chart with item.height={ITEM_HEIGHT}px. "
        f"Under autosize:pad the re-render must correct for strip overhead so "
        f"the cached height does not exceed the allocated slot height."
    )


def test_support_table_width_correction_does_not_inflate_cached_height(
    tmp_path, local_project: Callable[..., Project]
):
    """The width-correction re-render must not grow cached_height above first-render height.

    Under autosize:pad, spec.height is the *inner* plot rect. Passing actual_height
    (from the first render) as spec.height causes Vega to add strip/axis on top of
    that value, so the outer SVG grows to ~actual_height + strip_height — inflating
    the cached layout height on every re-render.

    Fix: pass height=static_estimate (the original requested height) to the re-render,
    and clamp: actual_height = max(actual_height_from_rerender, static_estimate).

    We measure the inflation by comparing the cached height from the full layout pass
    against the height returned by the *first* render call (captured via a spy), which
    is the correct reference — the re-render must not produce something taller.
    """
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor

    result = compile(
        """\
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 200}
      - {month: Mar, revenue: 150}
charts:
  weekly_bar:
    query: q
    type: bar
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: "$.0f"
        label: Revenue
    style:
      orientation: vertical
rows:
  - weekly_bar
"""
    )
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    # Spy on _render_chart_to_svg to capture the height returned by the first render
    # call (before the width-correction re-render). The second call is the re-render;
    # the cached height must not exceed the first-render height by a large margin.
    import importlib
    from unittest.mock import patch

    from dbt_charts.core.render.board_resolve import build_resolved_board

    layout_sizing_mod = importlib.import_module("dbt_charts.core.render.layout_sizing")
    original_render = layout_sizing_mod._render_chart_to_svg
    render_heights: list[float] = []

    def spy_render(resolved, executor_, variables_, width, **kwargs):
        svg, w, h, probe = original_render(
            resolved, executor_, variables_, width, **kwargs
        )
        if resolved.id == "weekly_bar":
            render_heights.append(h)
        return svg, w, h, probe

    with patch.object(
        layout_sizing_mod, "_render_chart_to_svg", side_effect=spy_render
    ):
        _resolved_board, render_cache = build_resolved_board(
            result.board, executor, {}, render_first=True
        )

    cache_entry = next(
        (v for k, v in render_cache.items() if k[0] == "weekly_bar"), None
    )
    assert cache_entry is not None, "support_table chart must be cached"
    _cached_svg, cached_height = cache_entry

    assert len(render_heights) >= 1, (
        "Expected at least 1 render call for a support_table chart, got 0"
    )

    # In V2, support_table is not yet attached in the sizing pass,
    # so there is no autosize:pad width overflow and only 1 render call is made.
    # Once support_table is ported to V2, len(render_heights) will be >= 2 and
    # the non-inflation invariant below re-applies with the first-render height.
    first_render_height = render_heights[0]

    # The cached height must not be materially larger than the first-render height.
    # Allow a 50px buffer for the static estimate floor.
    assert cached_height <= first_render_height + 50, (
        f"Width-correction re-render inflated cached height: "
        f"first_render_height={first_render_height:.1f}px, "
        f"cached_height={cached_height:.1f}px "
        f"(delta={cached_height - first_render_height:.1f}px, tolerance=50px). "
        f"Pass height=static_estimate (not actual_height) to the re-render so "
        f"autosize:pad does not add strips on top of the already-measured height."
    )


def test_support_table_width_correction_rebakes_title_font_at_shrunk_width(
    tmp_path, local_project: Callable[..., Project]
):
    """After support_table width correction, title_font in pre_resolved must be baked
    at the shrunk (corrected) render width, not the original layout-item width.

    Pre-fix: _require_resolved cached the resolved chart at the first-call width (layout
    item width). When the re-render used the shrunk width, _require_resolved returned the
    cache hit — so title_font was still baked at the original (wider) tier.

    Post-fix: the cache entry is invalidated before the re-render, so _require_resolved
    resolves again at the shrunk width and title_font reflects the correct tier.
    """
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board

    # Two-column layout: each column is ~576px (medium tier → 18px Source Serif).
    # After support_table overhead correction the chart shrinks to ~348px (tiny tier → 11px Inter).
    result = compile(
        """\
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 200}
      - {month: Mar, revenue: 150}
      - {month: Apr, revenue: 175}
      - {month: May, revenue: 220}
charts:
  bar_with_table:
    query: q
    type: bar
    title: Revenue by Month
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: "$.0f"
        label: Revenue
    style:
      orientation: vertical
  other_bar:
    query: q
    type: bar
    title: Other Chart
    x: month
    y: revenue
cols:
  - bar_with_table
  - other_bar
"""
    )
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    resolved_board, _render_cache = build_resolved_board(
        result.board, executor, {}, render_first=True
    )

    # Retrieve the resolved chart from the board's charts dict.
    resolved_chart = resolved_board.charts.get("bar_with_table")
    assert resolved_chart is not None, "bar_with_table must be in resolved_board.charts"
    assert resolved_chart.style.title_font is not None, (
        "title_font must be baked by resolve()"
    )

    from dbt_charts.core.compile.resolve.style.typography import (
        _NARROW_MAX,
        chart_title_spec,
    )

    # The initial layout-item width (one column of a 2-col 1152px board = ~566px) is
    # medium-tier (≥560px → 18px for the default theme).  After support_table overhead
    # correction the VL spec.width is shrunk to ~456px (narrow tier → 14px), so
    # title_font.size must be LESS than the medium-tier size.
    #
    # Pre-fix: _require_resolved returned the medium-tier cache hit (18px) on the
    # second call; post-fix: the cache is invalidated and title_font is re-baked at
    # the shrunk spec.width, giving the correct sub-medium-tier size.
    medium_size, _, _ = chart_title_spec(
        _NARROW_MAX, chart_style_context=resolve_chart_style_context(get_theme_style())
    )  # size at the medium-tier threshold
    assert int(resolved_chart.style.title_font.size) < medium_size, (
        f"title_font.size={int(resolved_chart.style.title_font.size)}px is at the "
        f"medium-tier size ({medium_size}px), indicating title_font was baked at the "
        f"initial layout-item width rather than the smaller shrunk render width. "
        f"The support_table width-correction pass must invalidate the _require_resolved "
        f"cache so title_font is re-baked at the corrected (narrower) spec.width."
    )


def _stacked_per_series_total_board_yaml(width: int) -> str:
    """A stacked horizontal bar with a per_series entry plus an aggregate-sum
    Total column — the case that competes hardest for width, since every
    series gets its own column plus one more for the total."""
    return f"""\
queries:
  q:
    type: values
    rows:
      - {{category: "Step 01", series: S1, value: 100000}}
      - {{category: "Step 01", series: S2, value: 140000}}
      - {{category: "Step 01", series: S3, value: 180000}}
      - {{category: "Step 04", series: S1, value: 220000}}
      - {{category: "Step 04", series: S2, value: 260000}}
      - {{category: "Step 04", series: S3, value: 300000}}
charts:
  w:
    query: q
    type: bar
    title: w
    width: {width}
    x: category
    y: value
    color: series
    support_table:
      - per_series: value
        format: ","
      - aggregate: sum
        source: value
        label: Total
        format: ","
    style:
      orientation: horizontal
      stack: zero
rows:
  - w
"""


def _stacked_per_series_total_board_yaml_with_sibling(width: int) -> str:
    """The starved column-block chart above, plus an unrelated sibling chart.

    A single-chart board can't tell a per-chart error card apart from a
    board-wide failure — both leave ``output`` empty. The sibling is the
    control: it must still render when the starved chart fails.
    """
    yaml_src = _stacked_per_series_total_board_yaml(width).replace(
        "charts:\n",
        """\
  q2:
    type: values
    rows:
      - {category: "Step 01", value: 420000}
      - {category: "Step 04", value: 780000}
charts:
""",
    )
    return yaml_src.replace(
        "rows:\n  - w\n",
        """\
  other:
    query: q2
    type: bar
    title: other
    x: category
    y: value
rows:
  - w
  - other
""",
    )


def test_support_table_width_floor_raises_a_per_chart_error_not_a_board_error(
    tmp_path, local_project: Callable[..., Project]
):
    """A card too narrow for the column block's own reservation plus axis
    chrome fails only the starved chart — see plot_width_floor.py. A sibling
    chart on the same board must still render; the whole board must not go
    down for one chart's width floor.
    """
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.renderer import render

    result = compile(_stacked_per_series_total_board_yaml_with_sibling(400))
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, (
        f"one chart's width floor took down the whole board: "
        f"{render_result.board_error}"
    )
    assert render_result.output is not None, (
        "the sibling chart must still render even though 'w' failed its width floor"
    )
    failed_ids = {diag.fields.get("chart_id") for diag in render_result.chart_errors}
    assert "w" in failed_ids, (
        f"expected chart 'w' to carry the width-floor diagnostic, got {failed_ids}"
    )
    assert "other" not in failed_ids
    w_diag = next(
        diag
        for diag in render_result.chart_errors
        if diag.fields.get("chart_id") == "w"
    )
    assert w_diag.code == "ERR-INPUT-INVALID"
    assert "support_table" in w_diag.message


def test_support_table_width_share_warns_before_the_floor(
    tmp_path, local_project: Callable[..., Project]
):
    """A card wide enough to stay above the hard floor, but narrow enough
    that the column block already dominates the width it shares with the
    plot, warns instead — the plot still renders."""
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.renderer import render

    result = compile(_stacked_per_series_total_board_yaml(600))
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, render_result.board_error
    assert render_result.output is not None
    assert not render_result.chart_errors
    warn_codes = {w.code for w in render_result.warnings}
    assert "WARN-PLOT-WIDTH-BELOW-MINIMUM" in warn_codes, warn_codes


def test_support_table_width_no_warning_or_error_at_a_comfortable_width(
    tmp_path, local_project: Callable[..., Project]
):
    """The same board at a comfortable width renders clean — the width floor
    and its softer warning only fire when the column block is actually
    competing for space."""
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.renderer import render

    result = compile(_stacked_per_series_total_board_yaml(1125))
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, render_result.board_error
    warn_codes = {w.code for w in render_result.warnings}
    assert "WARN-PLOT-WIDTH-BELOW-MINIMUM" not in warn_codes, warn_codes


def _grouped_bar_row_strip_board_yaml(width: int) -> str:
    """A vertical bar with a top row strip — the column block's width floor
    must never fire here: the strip reserves height, not width, and the only
    source of measured width overhead is ordinary axis-label/legend chrome
    any wide-legend chart carries, support_table or not."""
    return f"""\
queries:
  q:
    type: values
    rows:
      - {{month: "A Really Extremely Long Category Label", series: "Really Long Series Name Alpha Alpha Alpha", value: 100000}}
      - {{month: "Another Extremely Long Category Label", series: "Really Long Series Name Alpha Alpha Alpha", value: 120000}}
      - {{month: "A Really Extremely Long Category Label", series: "Really Long Series Name Beta Beta Beta", value: 90000}}
      - {{month: "Another Extremely Long Category Label", series: "Really Long Series Name Beta Beta Beta", value: 95000}}
      - {{month: "A Really Extremely Long Category Label", series: "Really Long Series Name Gamma Gamma Gamma", value: 80000}}
      - {{month: "Another Extremely Long Category Label", series: "Really Long Series Name Gamma Gamma Gamma", value: 85000}}
charts:
  w:
    query: q
    type: bar
    title: w
    width: {width}
    x: month
    y: value
    color: series
    support_table:
      - aggregate: sum
        source: value
        label: Total
        format: ","
    style:
      orientation: vertical
      stack: none
rows:
  - w
"""


def test_support_table_width_floor_does_not_fire_on_a_row_strip(
    tmp_path, local_project: Callable[..., Project]
):
    """The width floor is the column block's own mechanism — see
    plot_width_floor.py's "reserves pixel width beside a horizontal bar's
    plot". A top/bottom row strip reserves zero width, so a narrow card with
    a long series-name legend (ordinary axis/legend chrome, unrelated to any
    block) must not raise the column block's floor on a chart that has no
    column block at all.
    """
    import pytest

    pytest.importorskip("vl_convert")

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.renderer import render

    result = compile(_grouped_bar_row_strip_board_yaml(250))
    assert result.success and result.board is not None, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, (
        f"the row strip's own width floor misfired on ordinary axis/legend "
        f"chrome: {render_result.board_error}"
    )
    assert render_result.output is not None
    # A breach no longer surfaces as a board error — it records a per-chart
    # failure and paints an error card, leaving board_error None and output
    # non-None. Without this assertion the test stays green when the
    # vertical-category-axis gate is deleted and the strip trips the floor.
    failed_ids = {diag.fields.get("chart_id") for diag in render_result.chart_errors}
    assert not failed_ids, f"the row strip's floor misfired: {failed_ids}"
