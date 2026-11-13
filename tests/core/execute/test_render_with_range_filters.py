"""``Executor.render_with_range_filters``: the SQL a query executes as and the date
ranges its filters emitted, derived without resolving a source or connecting."""

from __future__ import annotations

import json
import socket
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from dbt_charts._engine import CompileError
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import plan_for_board
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.template.parameterized import (
    DateRangeFilter,
    render_parameterized_with_queries,
)
from dbt_charts.core.execute.date_range_axis import proven_range
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.plan import PlanMemo, render_with_range_filters, utc_now
from dbt_charts.core.execute.plan_reader import env_sources
from dbt_charts.core.project import InMemoryBoard

from ..._run import Run

_RANGE = {"date_range": ["2026-03-01", "2026-03-31"]}
_FILTERED = "WHERE {{ filter_date_range('created_at', date_range) }}"


def _board(sql: str, source: str | None) -> str:
    source_line = "" if source is None else f"    source: {source}\n"
    return f"""
variables:
  date_range:
    input: daterange
queries:
  q:
    type: sql
{source_line}    sql: |
      {sql}
charts:
  c:
    query: q
    type: bar
    x: week
    y: n
rows:
  - c
"""


def _executor(
    yaml_text: str,
    tmp_path: Path,
    sources: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
) -> Run:
    """A run over *yaml_text*, planned under the date range unless *variables* say otherwise."""
    if sources:
        (tmp_path / "dbt_charts.yml").write_text(yaml.dump({"sources": sources}))
    result = compile_yaml(
        yaml_text,
        project_sources=ProjectSourcesConfig(sources=sources or {}),
    )
    assert result.success and result.board is not None, result.errors
    return Run(
        result.board,
        env=build_exec_env(FilesystemProject(tmp_path)),
        variables=_RANGE if variables is None else variables,
    )


def _dbt_project(tmp_path: Path, with_manifest: bool = True) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: p\n")
    (tmp_path / "profiles.yml").write_text(
        yaml.dump(
            {
                "p": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "duckdb",
                            "path": str(tmp_path / "w.duckdb"),
                            "threads": 1,
                        }
                    },
                }
            }
        )
    )
    if with_manifest:
        manifest = tmp_path / "target" / "manifest.json"
        manifest.parent.mkdir(exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "nodes": {
                        "model.p.orders": {
                            "resource_type": "model",
                            "name": "orders",
                            "schema": "main",
                            "alias": "orders",
                        }
                    },
                    "sources": {},
                }
            )
        )


def test_source_less_dbt_jinja_query_reports_its_range(
    tmp_path: Path,
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(
            f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
            "p",
        ),
        tmp_path,
        {"p": {"type": "dbt_profile"}},
    )
    rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "main.orders" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_declared_source_reports_its_range_without_resolving_it(
    tmp_path: Path,
) -> None:
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        tmp_path,
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    with patch(
        "dbt_charts.core.execute.plan.resolve_planned_source",
        side_effect=socket.gaierror("no dns"),
    ):
        rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    assert rendered[2] == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_source_less_query_reports_its_range_through_the_implicit_dbt_profile(
    tmp_path: Path,
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "p"),
        tmp_path,
    )
    # The engine refuses a SQL query with no source, so strip the plan's entry
    # to the source-less shape the implicit profile answers for.
    executor.ctx.plan["queries"]["q"]["source"] = None
    registry = executor.ctx.env
    assert registry.sources.runtime_config().implicit_source is not None
    rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "FROM orders WHERE" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_named_dbt_profile_source_reports_its_range_in_the_profile_dialect(
    tmp_path: Path,
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        tmp_path,
        {"wh": {"type": "dbt_profile"}},
    )
    rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "FROM orders WHERE" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_a_query_that_cannot_be_rendered_here_reports_nothing(
    tmp_path: Path,
) -> None:
    _dbt_project(tmp_path, with_manifest=False)
    executor = _executor(
        _board(
            f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
            "p",
        ),
        tmp_path,
    )
    assert render_with_range_filters(executor.ctx, "q") is None


def test_a_query_without_a_range_filter_is_not_rendered(
    tmp_path: Path,
) -> None:
    executor = _executor(
        _board("SELECT 1 AS week, 1 AS n FROM orders", "wh"),
        tmp_path,
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    with patch(
        "dbt_charts.core.execute.plan.render_parameterized_with_queries"
    ) as render_sql:
        assert render_with_range_filters(executor.ctx, "q") is None
    render_sql.assert_not_called()


def test_the_derivation_is_memoized_per_query_and_variables(
    tmp_path: Path,
) -> None:
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        tmp_path,
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    with patch(
        "dbt_charts.core.execute.plan.render_parameterized_with_queries",
        wraps=render_parameterized_with_queries,
    ) as spy:
        first = render_with_range_filters(executor.ctx, "q")
        assert render_with_range_filters(executor.ctx, "q") == first
        # One context holds one plan, so one set of variables; another plan is
        # another context with its own memo.
        later = replace(
            executor.under({"date_range": ["2026-04-01", "2026-04-30"]}),
            memo=PlanMemo(),
        )
        render_with_range_filters(later, "q")
    assert spy.call_count == 2


def test_a_query_written_in_another_dialect_renders_and_parses_in_it(
    tmp_path: Path,
) -> None:
    board = _board(
        f"SELECT DATE_TRUNC(created_at, WEEK) AS week, 1 AS n FROM orders {_FILTERED}",
        "wh",
    ).replace("    source: wh\n", "    source: wh\n    dialect: bigquery\n")
    executor = _executor(
        board,
        tmp_path,
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    sql, dialect, filters = rendered
    assert dialect == "bigquery"
    assert proven_range(sql, dialect, "week", filters) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
    )


def test_a_raw_sql_variable_on_the_dbt_path_is_bound_as_execution_binds_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbt_charts.core.execute.date_range_axis import proven_range

    # No `profile:` in dbt_project.yml means no implicit source, so a dbt-jinja
    # query naming no declared source is deferred to the dbt adapter.
    _dbt_project(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    monkeypatch.setenv("DBT_PROFILE", "p")
    board = _board(
        "SELECT created_at AS week, 1 AS n FROM {{ ref('orders') }} "
        "WHERE {{ extra_where }} AND "
        "{{ filter_date_range('created_at', date_range) }}",
        "dbt",
    ).replace("variables:\n", "variables:\n  extra_where:\n    input: text\n")
    executor = _executor(
        board,
        tmp_path,
        {"dbt": {"type": "dbt_profile"}},
        variables={**_RANGE, "extra_where": "status = 'paid' OR 1 = 1"},
    )
    rendered = render_with_range_filters(executor.ctx, "q")
    assert rendered is not None
    sql, dialect, filters = rendered
    # Bound as one value, as execution binds it: never spliced into the SQL.
    assert "WHERE 'status = ''paid'' OR 1 = 1' AND" in sql
    assert proven_range(sql, dialect, "week", filters) is not None


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
        f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}",
    ],
    ids=["dbt-jinja", "plain-sql"],
)
def test_an_undeclared_source_is_refused_by_the_plan_not_deferred_to_dbt(
    tmp_path: Path, sql: str
) -> None:
    _dbt_project(tmp_path)
    project = FilesystemProject(tmp_path)

    with pytest.raises(CompileError, match="nowhere"):
        plan_for_board(
            InMemoryBoard(_board(sql, "nowhere"), path=None),
            project,
            _RANGE,
            None,
            utc_now(),
            False,
            False,
            all_queries=True,
            sources=env_sources(build_exec_env(project)),
        )
