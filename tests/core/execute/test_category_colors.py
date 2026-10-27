"""Board-wide category-color planning: an empty result set must not poison a field.

`plan_board_category_colors`/`_observe`
(`execute/category_colors.py`) poison a field board-wide when a chart that
encodes it draws values `_observe` cannot use for a scale — non-string,
unanimously date-like. An empty result set took the same branch
(``if disqualified or not values: continue``), even though a chart with zero
rows cannot be mis-seated by anything — there is nothing to seat. That
contradicts the poisoning rationale itself ("a scale that cannot seat THIS
chart's values") and `_observe`'s own docstring ("Nulls are skipped, not
disqualifying"): under `dct serve`, a variable change that empties one
chart's result set silently drops the shared binding for every OTHER chart
on the board.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile, focus_on_chart
from dbt_charts.core.compile.resolve.style.category_colors import CategoryColorScale
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.category_colors import plan_board_category_colors
from dbt_charts.core.execute.executor import Executor

# A typed NULL needs a SQL source; a values column of only nulls has no type.
_SOURCES_DIR = Path(tempfile.mkdtemp(prefix="dct-category-colors-"))
(_SOURCES_DIR / "dbt_charts.yml").write_text(
    'sources:\n  mem:\n    type: duckdb\n    path: ":memory:"\n'
)


def _plan(yaml_content: str, focus: str | None = None) -> dict[str, CategoryColorScale]:
    project = FilesystemProject(_SOURCES_DIR)
    result = compile(yaml_content, project_sources=project.sources)
    assert result.success and result.board is not None, result.errors
    board = result.board if focus is None else focus_on_chart(result.board, focus)
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    scales = plan_board_category_colors(board, executor, {})
    return {s.field: s for s in scales}


_PINNED_TWO_CHARTS = """
queries:
  both:
    type: values
    rows:
      - {cat: Electronics, revenue: 10}
      - {cat: Tools, revenue: 20}
  one:
    type: values
    rows:
      - {cat: Electronics, revenue: 10}
style:
  charts:
    category_colors:
      cat:
        values:
          Electronics: "#111111"
          Tools: "#222222"
charts:
  a:
    query: both
    type: bar
    x: cat
    y: revenue
    color: cat
  b:
    query: one
    type: bar
    x: cat
    y: revenue
    color: cat
rows:
  - a
  - b
"""


def test_a_focused_chart_paints_its_pinned_value_the_board_color() -> None:
    """A pinned value must not change color just because the layout narrowed.

    Unpinned fields legitimately diverge under focus: planning them needs the
    sibling queries focusing exists to skip. A pinned one costs nothing —
    authoring a field bypasses the threshold, and a pin addresses a slot — so
    the focused render of chart `b` paints `Electronics` the authored
    `#111111`, exactly as the full board does.
    """
    full = _plan(_PINNED_TWO_CHARTS)
    focused = _plan(_PINNED_TWO_CHARTS, focus="b")
    assert full["cat"].overrides["Electronics"] == "#111111"
    assert focused["cat"].overrides["Electronics"] == "#111111"
    assert focused["cat"].slots["Electronics"] == full["cat"].slots["Electronics"]
    # `Tools` is drawn by chart `a` alone, so the focused render never sees it.
    # It is skipped rather than seated, which is what keeps the slot
    # `Electronics` owns identical on both sides.
    assert "Tools" not in focused["cat"].slots


_TWO_HEALTHY_PLUS_EMPTY = """
queries:
  all:
    type: values
    rows:
      - {category: Accessories, revenue: 10}
      - {category: Electronics, revenue: 30}
      - {category: Tools, revenue: 20}
  empty:
    type: values
    rows: []
charts:
  bar_healthy:
    query: all
    type: bar
    x: revenue
    y: revenue
    color: category
  donut_healthy:
    query: all
    type: donut
    theta: revenue
    color: category
  bar_empty:
    query: empty
    type: bar
    x: revenue
    y: revenue
    color: category
rows:
  - bar_healthy
  - donut_healthy
  - bar_empty
"""


class TestEmptyResultSetDoesNotPoison:
    def test_the_field_still_binds(self) -> None:
        plan = _plan(_TWO_HEALTHY_PLUS_EMPTY)
        assert "category" in plan, plan

    def test_the_scale_covers_every_value_the_healthy_charts_draw(self) -> None:
        scale = _plan(_TWO_HEALTHY_PLUS_EMPTY)["category"]
        assert set(scale.slots) == {"Accessories", "Electronics", "Tools"}


_WHOLE_COLUMN_NULL = """
queries:
  all:
    type: values
    rows:
      - {category: Accessories, revenue: 10}
      - {category: Electronics, revenue: 30}
      - {category: Tools, revenue: 20}
  nulls:
    source: mem
    sql: >-
      SELECT CAST(NULL AS VARCHAR) AS category, 5 AS revenue
      UNION ALL SELECT CAST(NULL AS VARCHAR), 6
charts:
  bar_healthy:
    query: all
    type: bar
    x: revenue
    y: revenue
    color: category
  donut_healthy:
    query: all
    type: donut
    theta: revenue
    color: category
  bar_nulls:
    query: nulls
    type: bar
    x: revenue
    y: revenue
    color: category
rows:
  - bar_healthy
  - donut_healthy
  - bar_nulls
"""


class TestWholeColumnNullDoesNotPoison:
    """`_observe`'s own docstring: "Nulls are skipped, not disqualifying" —
    a column that is null in every row still isn't a shape disqualification,
    the same way an empty result set isn't. Two healthy charts satisfy the
    binding threshold on their own, isolating the poisoning bug from the
    ordinary "fewer than two charts" decline."""

    def test_a_chart_whose_color_column_is_entirely_null_does_not_poison(
        self,
    ) -> None:
        plan = _plan(_WHOLE_COLUMN_NULL)
        assert "category" in plan, plan
