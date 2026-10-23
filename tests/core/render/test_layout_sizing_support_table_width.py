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

from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project


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
