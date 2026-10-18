"""Tests for dbt_charts.agent_api.describe.describe_project — the no-board overview."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject

_CONFIG = """\
sources:
  warehouse:
    type: duckdb
    path: wh.duckdb
"""

_SALES = """\
title: Sales
queries:
  revenue:
    sql: SELECT month, SUM(amount) AS total FROM analytics.orders GROUP BY 1
    source: warehouse
charts:
  trend:
    query: revenue
    type: line
    x: month
    y: total
    title: Revenue Trend
rows:
  - trend
"""

_CUSTOMERS = """\
title: Customers
queries:
  c:
    sql: SELECT * FROM analytics.orders JOIN analytics.customers USING (id)
    source: warehouse
charts:
  tbl:
    query: c
    type: table
rows:
  - tbl
"""

_BROKEN = (
    "title: Broken\ncharts:\n  x:\n    query: nope\n    type: line\nrows:\n  - x\n"
)

_TEMPLATED = """\
title: Templated
queries:
  q:
    sql: EXPLAIN SELECT * FROM analytics.orders
    source: warehouse
charts:
  t:
    query: q
    type: table
rows:
  - t
"""


def _project(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    boards: dict[str, str],
    config: str | None = _CONFIG,
) -> FilesystemProject:
    if config is not None:
        (tmp_path / "dbt_charts.yml").write_text(config)
    (tmp_path / "charts").mkdir()
    for name, body in boards.items():
        (tmp_path / "charts" / name).write_text(body)
    return local_project(tmp_path)


def test_overview_lists_config_sources_boards_and_tables(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    project = _project(
        tmp_path, local_project, {"sales.yml": _SALES, "customers.yml": _CUSTOMERS}
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n")
    (tmp_path / "wh.duckdb").write_bytes(b"")

    overview = describe_project(project)

    assert overview.config_file == "dbt_charts.yml"
    assert [s.name for s in overview.configured_sources] == ["warehouse"]
    assert overview.data_files == ["./data/orders.csv"]
    assert list(overview.database_files) == ["wh.duckdb"]
    assert "type: duckdb" in overview.database_files["wh.duckdb"]
    boards = {b.path: b for b in overview.boards}
    assert boards["charts/sales.yml"].title == "Sales"
    assert boards["charts/sales.yml"].url == "/sales/"
    assert {t.table: t.boards for t in overview.tables} == {
        "analytics.orders": ["charts/customers.yml", "charts/sales.yml"],
        "analytics.customers": ["charts/customers.yml"],
    }
    assert overview.incomplete == []
    assert all(b.charts == [] for b in overview.boards)


def test_incomplete_names_uncompilable_and_undetermined_boards(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    project = _project(
        tmp_path,
        local_project,
        {"sales.yml": _SALES, "broken.yml": _BROKEN, "templated.yml": _TEMPLATED},
    )

    overview = describe_project(project)

    assert {i.board: i.reason for i in overview.incomplete} == {
        "charts/broken.yml": "does not compile",
        "charts/templated.yml": "cannot determine tables read by query q",
    }
    assert [t.table for t in overview.tables] == ["analytics.orders"]
    broken = next(b for b in overview.boards if b.path == "charts/broken.yml")
    assert broken.title is None


def test_all_adds_each_boards_charts(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    project = _project(tmp_path, local_project, {"sales.yml": _SALES})

    overview = describe_project(project, include_charts=True)

    (chart,) = overview.boards[0].charts
    assert (chart.name, chart.type, chart.title, chart.query) == (
        "trend",
        "line",
        "Revenue Trend",
        "revenue",
    )


def test_project_with_data_files_and_no_config(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    (tmp_path / "orders.csv").write_text("a\n1\n")
    overview = describe_project(local_project(tmp_path))

    assert overview.config_file is None
    assert overview.configured_sources == []
    assert overview.data_files == ["./orders.csv"]
    assert overview.boards == []


_MARKDOWN_BOARD = """\
---
board:
  title: Notes
  queries:
    m:
      sql: SELECT * FROM analytics.events
      source: warehouse
  charts:
    ev:
      query: m
      type: table
---

# Events

{{ chart ev }}
"""

_CALLOUT = """\
title: Callouts
queries:
  r:
    sql: SELECT * FROM analytics.orders
    source: warehouse
charts:
  note:
    type: callout
    message: hello
  inl:
    type: table
    query:
      sql: SELECT * FROM analytics.orders
      source: warehouse
  named:
    type: table
    query: r
rows:
  - note
  - inl
  - named
"""


def test_markdown_boards_are_listed_and_their_tables_counted(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    project = _project(tmp_path, local_project, {"notes.md": _MARKDOWN_BOARD})

    overview = describe_project(project)

    assert [b.path for b in overview.boards] == ["charts/notes.md"]
    assert overview.boards[0].title == "Notes"
    assert [(t.table, t.boards) for t in overview.tables] == [
        ("analytics.events", ["charts/notes.md"])
    ]
    assert overview.incomplete == []


def test_charts_without_a_query_have_none_and_inline_is_flagged(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api.describe import describe_project

    project = _project(tmp_path, local_project, {"c.yml": _CALLOUT})
    overview = describe_project(project, include_charts=True)

    charts = {c.name: c for c in overview.boards[0].charts}
    assert charts["note"].query is None
    assert charts["inl"].inline is True
    assert charts["named"].query == "r"
    assert charts["named"].inline is False
