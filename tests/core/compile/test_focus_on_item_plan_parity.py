"""A fence's focused plan reads what ``focus_on_item`` keeps for that fence.

The markdown page renders each ``charts`` fence as the top-level layout item it
compiles to, focused with ``focus_on_item``; ``_engine.plan(only_charts=...)``
must plan the charts that item places, and the variables they read, however the
fence declares them (inline, or as a nested board of its own).
"""

from pathlib import Path

import pytest

from dbt_charts import _engine
from dbt_charts.agent_api.markdown import chart_fences
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import sql_analysis
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.normalize.chart_focus import focus_on_item
from dbt_charts.core.execute.query_plan import parse_query_plan
from dbt_charts.core.project import InMemoryBoard

_NOW = 1_770_000_000

_SHARED_QUERY = """\
```charts
queries:
  sales:
    columns: [month, revenue]
    values:
      - [Jan, 100]
charts:
  first:
    type: bar
    query: sales
    x: month
    y: revenue
rows:
  - first
```

Then the same query as a line.

```charts
type: line
query: sales
x: month
y: revenue
```
"""

_NESTED_TABLE = """\
```charts
charts:
  draw:
    type: table
    query:
      columns: [v]
      values:
        - [drawn]
rows:
  - draw
```
"""

_NESTED_VARIABLE = """\
```charts
variables:
  region:
    input: text
    default: east
queries:
  sales:
    sql: select 1 as n where r = '{{ region }}'
    source: db
charts:
  total:
    type: kpi
    query: sales
    value: n
rows:
  - total
```
"""


_NESTED_UNREAD_VARIABLE = """\
```charts
variables:
  region:
    input: text
    default: east
  unused:
    input: text
    default: never
queries:
  sales:
    sql: select 1 as n where r = '{{ region }}'
    source: db
charts:
  total:
    type: kpi
    query: sales
    value: n
rows:
  - total
```
"""


class _Sqlglot:
    def analyze_all(self, requests: list[dict[str, str | None]]) -> list[dict]:
        return sql_analysis.analyze_all(requests, {})


class _Files:
    def __init__(self, page: str) -> None:
        self.page = page

    def read(self, path: str) -> str | None:
        return self.page if path == "charts/page.md" else None

    def list(self, dir: str) -> list[str]:
        return []

    def exists(self, path: str) -> bool:
        return path == "charts/page.md"


def _kept_variables(board: Board) -> set[str]:
    """What ``focus_on_item`` keeps: the root's variables a chart reads, and every variable of
    every nested board in the item, read or not."""
    kept = set(board.variables)
    for item in board.layout.items:
        if item.board is not None:
            kept |= _kept_variables(item.board)
    return kept


@pytest.mark.parametrize(
    ("page", "left_out"),
    [
        (_SHARED_QUERY, set()),
        (_NESTED_TABLE, set()),
        (_NESTED_VARIABLE, set()),
        # `focus_on_item` keeps a nested board's variables wholesale; the plan holds only the ones
        # a named chart depends on.
        (_NESTED_UNREAD_VARIABLE, {"unused"}),
    ],
    ids=["shared-query", "nested-table", "nested-variable", "nested-unread-variable"],
)
def test_a_fence_plans_what_focus_on_item_keeps(
    page: str, left_out: set[str], tmp_path: Path
) -> None:
    project = FilesystemProject(tmp_path)
    compiled = compile_file(InMemoryBoard(page, path=project.path("charts/page.md")))
    assert compiled.board is not None, compiled.errors
    engine = _engine.compile(
        _Files(page),
        "charts/page.md",
        boards_root=".",
        today="2026-11-06",
        default_theme="clarity",
        html_policy_ceiling=None,
        sources={},
        cache_root=(True, "24h", False),
        source_caches={},
        implicit_source=None,
        sql=_Sqlglot(),
    )
    for fence in chart_fences(page):
        focused = focus_on_item(compiled.board, fence.row)
        ids = [c.id for c in focused.placed_charts() if c.id]
        plan = parse_query_plan(
            _engine.plan(engine, "{}", "{}", _NOW, False, False, True, ids)
        )
        kept = _kept_variables(focused)
        assert left_out <= kept
        assert set(plan["variables"]["query"]) == kept - left_out
        queries = {c.query.model_dump_json(): c.query for c in focused.placed_charts()}
        assert len(plan["queries"]) == len(queries)
        kept_sql = {q.sql for q in queries.values() if q.query_type == "sql"}
        planned_sql = {q["sql"] for q in plan["queries"].values() if q["type"] == "sql"}
        assert planned_sql == kept_sql
