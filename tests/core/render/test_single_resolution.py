"""Single-resolution sizing: the sizing pass uses the content-sized dashboard
width while preserving per-family height rules.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# Shared board YAML: KPI + bar (cols, aspect-ratio-aligned) + table (row-count)
# ---------------------------------------------------------------------------

_BOARD_YAML = """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
      - {month: Mar, revenue: 120}
charts:
  kpi1:
    type: kpi
    query: q
    value: revenue
    label: Revenue
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
  table1:
    query: q
    type: table
rows:
  - cols:
      - kpi1
      - bar1
  - table1
"""


def _make_executor(
    board: Any, tmp_path: Path, local_project: Callable[..., Project]
) -> Any:
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry

    registry = build_adapter_registry(local_project(tmp_path), read_only=True)
    return Executor(board, adapter_registry=registry)


# ---------------------------------------------------------------------------
# Item heights follow the content-sized dashboard width
# ---------------------------------------------------------------------------

# Pinned from the content-sized pass at the resolved 648px dashboard width.
_EXPECTED_HEIGHTS: dict[str, float] = {
    "kpi1": 338.6666666666667,
    "bar1": 338.6666666666667,  # col-aligned to match kpi1 (and/or bar aspect)
    # 3 data rows at row_height + the header as the renderer measures it
    # (one line: the headers fit).
    "table1": 138.00,
}


def test_item_heights_follow_content_width(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    """Sizing item heights use the intrinsic dashboard width.

    Covers the three per-family height rules at the measured width:
    - KPI: static get_chart_content_height (no V2 render)
    - bar (Vega aspect-ratio): render-first, col-aligned with KPI
    - table: row-count-based _get_table_height_from_data

    Uses build_resolved_board (the real entry point) so the full V2 sizing
    path — single-resolution with pre_resolved — is exercised.
    """
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.render.board_resolve import build_resolved_board

    reset_config()
    result = compile(_BOARD_YAML)
    assert result.success, result.errors
    assert result.board is not None

    executor = _make_executor(result.board, tmp_path, local_project)
    resolved_board, _ = build_resolved_board(
        result.board, executor, {}, render_first=True
    )

    def collect_heights(items: Any) -> dict[str, float]:
        """Walk resolved layout items, collecting chart_id → height."""
        heights: dict[str, float] = {}
        for item in items:
            if item.chart is not None:
                heights[item.chart.id] = item.height
            elif item.board is not None and item.board.layout:
                heights.update(collect_heights(item.board.layout.items or []))
        return heights

    got = collect_heights(resolved_board.layout.items or [])

    for chart_id, expected in _EXPECTED_HEIGHTS.items():
        assert chart_id in got, f"chart {chart_id!r} not found in resolved layout"
        assert got[chart_id] == expected, (
            f"height for {chart_id!r} changed: expected {expected}, got {got[chart_id]}. "
            "Content sizing must preserve per-family height rules exactly."
        )
