"""``Executor.render_with_range_filters``: the SQL a query executes as and the date
ranges its filters emitted, derived without resolving a source or connecting."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.template.parameterized import DateRangeFilter
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.date_range_axis import proven_range

_RANGE = {"date_range": ["2026-03-01", "2026-03-31"]}
_FILTERED = "WHERE {{ filter_date_range('created_at', date_range) }}"


def _board(sql: str, source: str) -> str:
    source_line = f"    source: {source}\n"
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
    project: FilesystemProject,
    sources: dict[str, Any] | None = None,
) -> Executor:
    result = compile_yaml(
        yaml_text, project_sources=ProjectSourcesConfig(sources=sources or {})
    )
    assert result.success and result.board is not None, result.errors
    return Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
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
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(
            f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
            "p",
        ),
        local_project(tmp_path),
    )
    rendered = executor.render_with_range_filters("q", _RANGE)
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "main.orders" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_declared_source_reports_its_range_without_resolving_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        local_project(tmp_path),
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    registry = executor.adapter_registry
    with patch.object(
        registry, "resolve_query_source", side_effect=socket.gaierror("no dns")
    ):
        rendered = executor.render_with_range_filters("q", _RANGE)
    assert rendered is not None
    assert rendered[2] == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_source_less_query_reports_its_range_through_the_implicit_dbt_profile(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "p"),
        local_project(tmp_path),
        {"p": {"type": "dbt_profile"}},
    )
    registry = executor.adapter_registry
    assert registry.project_sources.implicit_source is not None
    source_less = executor.board.queries["q"].model_copy(update={"source": None})
    rendered = registry.render_with_range_filters(
        source_less, _RANGE, board=executor.board
    )
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "FROM orders WHERE" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_named_dbt_profile_source_reports_its_range_in_the_profile_dialect(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _dbt_project(tmp_path)
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        local_project(tmp_path),
        {"wh": {"type": "dbt_profile"}},
    )
    rendered = executor.render_with_range_filters("q", _RANGE)
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "FROM orders WHERE" in sql
    assert dialect == "duckdb"
    assert filters == [
        DateRangeFilter("created_at", date(2026, 3, 1), date(2026, 3, 31))
    ]


def test_a_query_that_cannot_be_rendered_here_reports_nothing(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _dbt_project(tmp_path, with_manifest=False)
    executor = _executor(
        _board(
            f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
            "p",
        ),
        local_project(tmp_path),
    )
    assert executor.render_with_range_filters("q", _RANGE) is None


def test_a_query_without_a_range_filter_is_not_rendered(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(
        _board("SELECT 1 AS week, 1 AS n FROM orders", "wh"),
        local_project(tmp_path),
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    with patch.object(
        executor.adapter_registry, "render_with_range_filters"
    ) as registry_render:
        assert executor.render_with_range_filters("q", _RANGE) is None
    registry_render.assert_not_called()


def test_the_derivation_is_memoized_per_query_and_variables(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "wh"),
        local_project(tmp_path),
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    registry = executor.adapter_registry
    with patch.object(
        registry, "render_with_range_filters", wraps=registry.render_with_range_filters
    ) as spy:
        first = executor.render_with_range_filters("q", _RANGE)
        assert executor.render_with_range_filters("q", _RANGE) == first
        executor.render_with_range_filters(
            "q", {"date_range": ["2026-04-01", "2026-04-30"]}
        )
    assert spy.call_count == 2


def test_a_dbt_project_naming_no_profile_reports_nothing(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _dbt_project(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("")
    executor = _executor(
        _board(
            f"SELECT created_at AS week, 1 AS n FROM {{{{ ref('orders') }}}} {_FILTERED}",
            "dbt",
        ),
        local_project(tmp_path),
    )
    assert executor.render_with_range_filters("q", _RANGE) is None


def test_a_query_written_in_another_dialect_renders_and_parses_in_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = _board(
        f"SELECT DATE_TRUNC(created_at, WEEK) AS week, 1 AS n FROM orders {_FILTERED}",
        "wh",
    ).replace("    source: wh\n", "    source: wh\n    dialect: bigquery\n")
    executor = _executor(
        board,
        local_project(tmp_path),
        {"wh": {"type": "duckdb", "path": ":memory:"}},
    )
    rendered = executor.render_with_range_filters("q", _RANGE)
    assert rendered is not None
    sql, dialect, filters = rendered
    assert dialect == "bigquery"
    assert proven_range(sql, dialect, "week", filters) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
    )


def test_a_raw_sql_variable_on_the_dbt_path_is_rendered_as_execution_renders_it(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
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
    executor = _executor(board, local_project(tmp_path))
    rendered = executor.render_with_range_filters(
        "q", {**_RANGE, "extra_where": "status = 'paid' OR 1 = 1"}
    )
    assert rendered is not None
    sql, dialect, filters = rendered
    assert "WHERE status = 'paid' OR 1 = 1 AND" in sql
    assert proven_range(sql, dialect, "week", filters) is None


def test_an_unknown_source_without_dbt_jinja_is_not_deferred_to_dbt(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    # Execution only hands a query to the dbt adapter when it uses dbt jinja.
    _dbt_project(tmp_path)
    executor = _executor(
        _board(f"SELECT created_at AS week, 1 AS n FROM orders {_FILTERED}", "nowhere"),
        local_project(tmp_path),
    )
    assert executor.render_with_range_filters("q", _RANGE) is None
