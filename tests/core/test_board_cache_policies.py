"""`BoardRenderResult.board_query_data_ages` covers every query behind the board.

Cloud is the consumer: it derives expires_at anchored on each query's actual
data age (data_as_of + ttl). A query missing from the list is invisible to
that computation. The list has to reflect all queries executed, including
inline chart queries, `#`-anchored refs, and layer queries.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute.env import build_exec_env

_BOARD = """
    title: Freshness
    source: db
    queries:
      named:
        sql: "SELECT 1 AS value"
        cache: 10m
    charts:
      c_named: {query: named, type: kpi, value: value}
      c_inline:
        type: kpi
        value: value
        query:
          sql: "SELECT 2 AS value"
          cache: 45s
      c_ref: {query: "shared.yml#totals", type: kpi, value: value}
    rows: [c_named, c_inline, c_ref]
    """


def _rendered_policies(tmp_path: Path) -> dict[str, str | None]:
    """Render the board the way Cloud does and return {query name: ttl}."""
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    (boards / "shared.yml").write_text(
        dedent(
            """
            title: Shared
            queries:
              totals:
                sql: "SELECT 3 AS value"
                source: db
                cache: 30s
            charts:
              c1: {query: totals, type: kpi, value: value}
            rows: [c1]
            """
        )
    )
    (boards / "board.yml").write_text(dedent(_BOARD))
    project = FilesystemProject(tmp_path)
    result = render_dashboard(
        board=project.path("charts/board.yml").read_board(),
        project=project,
        env=build_exec_env(project, read_only=False),
        result_cache=None,
        format="svg",
    )
    assert result.status != "failed", result.validation_errors or result.board_error
    return {name: policy.ttl for name, _da, policy in result.board_query_data_ages}


def test_inline_and_anchored_queries_carry_their_own_ttl(tmp_path: Path) -> None:
    """An inline chart query is the quick start's authoring form.

    `charts: {c: {query: "SELECT ..."}}` never gets a `queries:` entry, so a
    map built from the named registry omits it. A board written entirely that
    way hands Cloud an empty map, and `min(..., default=inf)` turns that into a
    snapshot that never expires — the freshness feature off, silently, for the
    boards most likely to exist.
    """
    policies = _rendered_policies(tmp_path)

    assert policies["named"] == "10m"
    assert policies["_inline_query_c_inline"] == "45s"
    assert policies["shared.yml#totals"] == "30s"


def test_a_board_of_only_inline_queries_still_reports_policies(
    tmp_path: Path,
) -> None:
    """The failure mode in its pure form: no `queries:` section at all.

    Nothing here is named, so a map built from the named registry is empty —
    and an empty map is not "this board has no data", it's "nothing bounds this
    snapshot's life". Cloud's `min(..., default=inf)` then stores the
    never-expires sentinel and serves the first render forever.
    """
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    (boards / "board.yml").write_text(
        dedent(
            """
            title: Live
            source: db
            charts:
              c1:
                type: kpi
                value: value
                query:
                  sql: "SELECT 1 AS value"
                  cache: false
            rows: [c1]
            """
        )
    )
    project = FilesystemProject(tmp_path)
    result = render_dashboard(
        board=project.path("charts/board.yml").read_board(),
        project=project,
        env=build_exec_env(project, read_only=False),
        result_cache=None,
        format="svg",
    )

    assert result.status != "failed", result.validation_errors or result.board_error
    assert [p.enabled for _name, _da, p in result.board_query_data_ages] == [False]
