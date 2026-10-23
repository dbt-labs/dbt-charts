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

# =============================================================================
# _compute_support_table_entry_dx unit tests
# =============================================================================


# =============================================================================
# title.offset vs padding.top for position:top support_table
# =============================================================================


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
