"""Chart-local palette capacity vs board-wide category-color scales.

`_bound_scales` (`compile/resolve/chart/_kwargs.py`) narrows the board's
planned scales to the fields a chart draws. A scale is planned against the
BOARD palette, but a chart's own EFFECTIVE palette -- a chart-local
`style.color.categorical.palette` override, or a nested board's shorter theme
-- can be shorter. On `main`, a scale whose highest
slot exceeds that shorter palette is handed to the chart anyway, and the
chart's own render then raises `ChartDataError` out of `category_colors._slot_for` --
turning a board that rendered fine before this feature existed into an
error, just because a sibling chart happens to share the field. The fix is
to decline the binding for THAT chart instead: `_bound_scales` drops any
scale it cannot seat, and the chart keeps the per-chart coloring it had
before binding existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor

# Board palette has plenty of capacity for the field (4 values, slots 0-3),
# but `narrow`'s own chart-local palette has only 3 stops -- too short to
# seat slot 3 ("D"). `wide` is what widens the field past the two-chart
# threshold; alone, `narrow` would never bind at all.
_BOARD = """
queries:
  cats4:
    type: values
    rows:
      - {category: A, status: Open, revenue: 10}
      - {category: B, status: Open, revenue: 20}
      - {category: C, status: Open, revenue: 30}
      - {category: D, status: Open, revenue: 40}
  cats_ad:
    type: values
    rows:
      - {category: A, status: Open, revenue: 10}
      - {category: D, status: Open, revenue: 40}
charts:
  wide:
    query: cats4
    type: bar
    x: status
    y: revenue
    color: category
  narrow:
    query: cats_ad
    type: bar
    x: status
    y: revenue
    color: category
    style:
      color:
        categorical:
          palette: ["#111111", "#222222", "#333333"]
rows:
  - wide
  - narrow
"""


def _resolve_board(
    yaml_content: str, resolve_errors: dict[str, Any] | None = None
) -> tuple[Any, Executor]:
    from dbt_charts.core.render.board_resolve import build_resolved_board

    result = compile(yaml_content)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    kwargs: dict[str, Any] = (
        {} if resolve_errors is None else {"resolve_errors": resolve_errors}
    )
    resolved_board, _ = build_resolved_board(
        result.board, executor, {}, render_first=False, **kwargs
    )
    return resolved_board, executor


class TestChartLocalPaletteCapacity:
    def test_a_sibling_with_a_shorter_local_palette_does_not_break_the_board(
        self,
    ) -> None:
        """Resolving the board must not raise; `wide` stays bound, `narrow`
        simply declines the binding it cannot safely seat."""
        resolved_board, _ = _resolve_board(_BOARD)
        wide = resolved_board.charts["wide"]
        narrow = resolved_board.charts["narrow"]
        assert any(s.field == "category" for s in wide.category_colors)
        assert narrow.category_colors == ()

    def test_the_narrow_chart_keeps_its_own_short_palette(self) -> None:
        """Declining the binding must not touch the chart's own effective
        palette -- it still resolves the chart-local override, unbound."""
        resolved_board, _ = _resolve_board(_BOARD)
        narrow = resolved_board.charts["narrow"]
        assert list(narrow.palette) == ["#111111", "#222222", "#333333"]


class TestPieAttachmentPaletteCapacity:
    """`pie.py`'s own `_bound_scales` call (feeding the attached-table swatch
    column, distinct from `_base_kwargs`'s wedge-fill scale) must decline
    the same way. `narrow_pie` draws only two board-bound values (A, D) but
    across enough tiny-share rows to force `full_table` mode -- the only
    path that reads `chart_category_colors` via `project_pie_table_rows`."""

    _MANY_ROWS = "\n".join(
        f"      - {{category: {'A' if i % 2 == 0 else 'D'}, revenue: 1}}"
        for i in range(20)
    )
    _BOARD = f"""
queries:
  cats4:
    type: values
    rows:
      - {{category: A, revenue: 10}}
      - {{category: B, revenue: 20}}
      - {{category: C, revenue: 30}}
      - {{category: D, revenue: 40}}
  cats_ad_many:
    type: values
    rows:
{_MANY_ROWS}
charts:
  wide:
    query: cats4
    type: bar
    x: revenue
    y: revenue
    color: category
  narrow_pie:
    query: cats_ad_many
    type: pie
    theta: revenue
    color: category
    style:
      color:
        categorical:
          palette: ["#111111", "#222222", "#333333"]
rows:
  - wide
  - narrow_pie
"""

    def test_a_narrow_donut_forced_into_full_table_mode_does_not_fail_to_resolve(
        self,
    ) -> None:
        resolve_errors: dict[str, Any] = {}
        resolved_board, _ = _resolve_board(self._BOARD, resolve_errors)
        assert resolve_errors == {}, resolve_errors
        pie = resolved_board.charts["narrow_pie"]
        assert pie.full_attachment is not None
        assert pie.category_colors == ()
