"""Regression: a composed query ({{ queries.X }}) that also uses {{ filter(...) }}
must execute cleanly end-to-end instead of raising CompilationError.

Root cause: reference resolution used to render the fully-expanded composed SQL,
which bound the raising compile-time filter()/filter_date_range() stubs. A query
that both composed another query AND filtered hit that stub before the adapter's
parameterized pass ever got a chance to bind filter() safely — the same pass that
already handled filter() fine for non-composed queries.

Resolution is now reference expansion only, so a filter helper reaches that pass
as author-written text and binds there like any other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.env import build_exec_env

from ..._run import Run


def _project_with_db_source(
    local_project: Callable[..., FilesystemProject],
) -> FilesystemProject:
    """A Project whose sources registry resolves 'db' to an in-memory DuckDB."""
    project = local_project(Path.cwd())
    # sources is a cached_property; pre-populate its cache via the instance dict.
    # vars() gives the writable __dict__ (typed dict[str, Any]) — a plain
    # project.__dict__[...] assignment trips pyright's read-only MappingProxyType.
    vars(project)["sources"] = ProjectSourcesConfig(
        sources={"db": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


def _build_board_and_executor(
    yaml_body: str, local_project: Callable[..., FilesystemProject]
) -> Run:
    result = compile(yaml_body)
    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    registry = build_exec_env(_project_with_db_source(local_project))
    return Run(result.board, registry, use_cache=False)


class TestComposedQueryWithFilter:
    """Integration: {{ queries.base }} composition + {{ filter(...) }} in the
    same query must run against a real SQL adapter without raising."""

    def test_composed_query_with_filter_executes_and_filters_rows(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        yaml_body = "\n".join(
            [
                "source: db",
                "variables:",
                "  status:",
                "    input: text",
                "    default: active",
                "queries:",
                "  base:",
                "    sql: |",
                "      SELECT 1 AS id, 'active' AS status",
                "      UNION ALL",
                "      SELECT 2 AS id, 'inactive' AS status",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT * FROM {{ queries.base }}",
                "      WHERE {{ filter('status', status) }}",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: id",
                "    y: id",
                "    query: composed",
            ]
        )
        executor = _build_board_and_executor(yaml_body, local_project)

        rows = executor.execute_query("composed", {"status": "active"})
        assert rows == [{"id": 1, "status": "active"}]

    def test_composed_query_filter_value_is_parameterized_not_interpolated(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The filter value still reaches the adapter as a bound parameter, not
        a string-interpolated literal — an injection payload matches no row."""
        yaml_body = "\n".join(
            [
                "source: db",
                "variables:",
                "  status:",
                "    input: text",
                "    default: active",
                "queries:",
                "  base:",
                "    sql: |",
                "      SELECT 1 AS id, 'active' AS status",
                "      UNION ALL",
                "      SELECT 2 AS id, 'inactive' AS status",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT * FROM {{ queries.base }}",
                "      WHERE {{ filter('status', status) }}",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: id",
                "    y: id",
                "    query: composed",
            ]
        )
        executor = _build_board_and_executor(yaml_body, local_project)

        # If interpolated, this OR payload would flip the filter and return
        # both rows. Parameterized, it matches no status value → zero rows.
        rows = executor.execute_query("composed", {"status": "active' OR '1'='1"})
        assert rows == []

    def test_composed_query_with_filter_date_range_executes(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        yaml_body = "\n".join(
            [
                "source: db",
                "variables:",
                "  created_range:",
                "    input: daterange",
                "    default: ['2025-01-01', '2025-12-31']",
                "queries:",
                "  base:",
                "    sql: |",
                "      SELECT 1 AS id, DATE '2025-02-01' AS created_at",
                "      UNION ALL",
                "      SELECT 2 AS id, DATE '2026-06-01' AS created_at",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT * FROM {{ queries.base }}",
                "      WHERE {{ filter_date_range('created_at', created_range) }}",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: id",
                "    y: id",
                "    query: composed",
            ]
        )
        executor = _build_board_and_executor(yaml_body, local_project)

        rows = executor.execute_query(
            "composed", {"created_range": ["2025-01-01", "2025-12-31"]}
        )
        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_composed_query_with_filter_date_range_empty_list_is_unfiltered(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A cleared date picker publishes `[]` (variables.js's unset spelling).
        The coercer must read that as unset rather than raising, so the query
        executes unfiltered instead of failing the whole board."""
        yaml_body = "\n".join(
            [
                "source: db",
                "variables:",
                "  created_range:",
                "    input: daterange",
                "    default: ['2025-01-01', '2025-12-31']",
                "queries:",
                "  base:",
                "    sql: |",
                "      SELECT 1 AS id, DATE '2025-02-01' AS created_at",
                "      UNION ALL",
                "      SELECT 2 AS id, DATE '2026-06-01' AS created_at",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT * FROM {{ queries.base }}",
                "      WHERE {{ filter_date_range('created_at', created_range) }}",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: id",
                "    y: id",
                "    query: composed",
            ]
        )
        executor = _build_board_and_executor(yaml_body, local_project)

        rows = executor.execute_query("composed", {"created_range": []})
        assert len(rows) == 2

    def test_composed_query_with_filter_date_range_includes_whole_end_day_for_timestamp(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """CAST(column AS DATE) truncates a TIMESTAMP to midnight before the
        BETWEEN compares it, so a row on the afternoon of the range's end date
        is included — the range is inclusive of the whole end day, not just
        midnight. This differs from the pre-cast behavior (which excluded any
        end-day timestamp after 00:00:00); pin the new, intended semantics."""
        yaml_body = "\n".join(
            [
                "source: db",
                "variables:",
                "  created_range:",
                "    input: daterange",
                "    default: ['2025-01-01', '2025-01-31']",
                "queries:",
                "  base:",
                "    sql: |",
                "      SELECT 1 AS id, TIMESTAMP '2025-01-01 00:00:00' AS created_at",
                "      UNION ALL",
                "      SELECT 2 AS id, TIMESTAMP '2025-01-31 00:00:00' AS created_at",
                "      UNION ALL",
                "      SELECT 3 AS id, TIMESTAMP '2025-01-31 13:00:00' AS created_at",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT * FROM {{ queries.base }}",
                "      WHERE {{ filter_date_range('created_at', created_range) }}",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: id",
                "    y: id",
                "    query: composed",
            ]
        )
        executor = _build_board_and_executor(yaml_body, local_project)

        rows = executor.execute_query(
            "composed", {"created_range": ["2025-01-01", "2025-01-31"]}
        )
        assert sorted(r["id"] for r in rows) == [1, 2, 3]
