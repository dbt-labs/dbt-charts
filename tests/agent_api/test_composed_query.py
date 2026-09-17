"""Regression tests for composed-query execution and validate paths.

Root causes fixed here:
1. `dct query BOARD NAME` failed on any query containing `{{ queries.X }}`
   with "Undefined variable: 'queries' is undefined".
2. `--validate` on a named query passed raw Jinja template text to sqlglot,
   producing false WARN-PARSE-ERROR on working SQL.
3. `dct query BOARD NAME` failed when the *referenced* query's own SQL still
   carried a template (a plain variable, `filter()`, or a literal) —
   `AdapterRegistry._compose_query_refs` inlines `{{ queries.X }}` in one
   non-recursive Jinja pass, so a template inside X's SQL was never rendered
   (dbt-labs/dbt-charts#12).
4. Fixing (3) via `resolve_query_references` also means `query_board` now
   runs a referenced query's `setup_sql` before the composed query, matching
   how execution already resolves the same board.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry

# ---------------------------------------------------------------------------
# Fixture — reproduction board from the task worksheet (DuckDB :memory:)
# ---------------------------------------------------------------------------

_DBT_CHARTS_YML = "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"

_PROBE_BOARD = """\
title: probe
source: mem
variables:
  n:
    input: number
    default: 3
queries:
  base:
    sql: SELECT 1 AS one, 7 AS seven
  wrapper:
    sql: "SELECT seven, {{ n }} AS n FROM {{ queries.base }}"
  plain:
    sql: SELECT {{ n }} AS n
charts:
  k:
    query: wrapper
    type: kpi
    value: seven
"""


@pytest.fixture
def probe_registry(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> AdapterRegistry:
    (tmp_path / "dbt_charts.yml").write_text(_DBT_CHARTS_YML)
    return build_adapter_registry(local_project(tmp_path), read_only=False)


@pytest.fixture
def probe_board_path(tmp_path: Path) -> Path:
    path = tmp_path / "probe.yml"
    path.write_text(_PROBE_BOARD)
    return path


# ---------------------------------------------------------------------------
# Root cause 1: execute path handles {{ queries.X }} composition
# ---------------------------------------------------------------------------


class TestComposedQueryExecution:
    """dct query BOARD NAME must run a query that references {{ queries.X }}."""

    def test_composed_query_returns_expected_rows(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            probe_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is True, result.errors
        # wrapper: SELECT seven, {{ n }} AS n FROM {{ queries.base }}
        # With n=3 (default): SELECT seven, 3 AS n FROM (SELECT 1 AS one, 7 AS seven) AS base
        assert result.columns == ["seven", "n"]
        assert result.data == [{"seven": 7, "n": 3}]

    def test_composed_query_matches_render_path_value(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The KPI value on the render path is 7; the query path must produce 7 too."""
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            probe_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is True, result.errors
        seven_values = [row["seven"] for row in result.data]
        assert seven_values == [7]

    def test_plain_query_still_works(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Non-composed queries must still execute correctly."""
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "plain",
            probe_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is True, result.errors
        assert result.data == [{"n": 3}]

    def test_var_override_on_composed_query(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """--var overrides should apply when executing a composed query."""
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            probe_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
            vars={"n": 9},
        )
        assert result.success is True, result.errors
        assert result.data == [{"seven": 7, "n": 9}]

    def test_undefined_variable_still_fails(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The fix must not make every unknown name resolve silently."""
        board_yaml = """\
source: mem
queries:
  bad:
    sql: SELECT {{ undefined_var }} AS x
charts:
  c:
    query: bad
    type: kpi
    value: x
"""
        path = tmp_path / "bad.yml"
        path.write_text(board_yaml)
        (tmp_path / "dbt_charts.yml").write_text(_DBT_CHARTS_YML)

        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "bad",
            path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is False
        # Pin the loud path: the failure must name the missing variable, not
        # be any incidental error.
        assert any("undefined_var" in e for e in result.errors), result.errors


# ---------------------------------------------------------------------------
# Root cause 2: validate path renders template before linting
# ---------------------------------------------------------------------------


class TestComposedQueryValidate:
    """--validate must render the SQL template before handing it to sqlglot."""

    def test_lookup_board_query_sql_reports_unknown_query_reference(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """lookup_board_query_sql (the --validate/--describe path) must return a
        clean error result when a query references an unknown {{ queries.X }}
        name, not raise JinjaError uncaught.

        Unlike a bare undefined variable (caught earlier, at compile time, as
        an unknown-variable ReferenceError), `{{ queries.X }}` is resolved via
        `_QueryNamespace.__getattr__` at render time, so a bad reference here
        only surfaces once `render_parameterized_with_queries` runs.
        """
        board_yaml = """\
source: mem
queries:
  base:
    sql: SELECT 1 AS one
  bad:
    sql: SELECT * FROM {{ queries.doesnotexist }}
charts:
  c:
    query: bad
    type: kpi
    value: one
"""
        path = tmp_path / "bad.yaml"
        path.write_text(board_yaml)
        (tmp_path / "dbt_charts.yml").write_text(_DBT_CHARTS_YML)

        from dbt_charts.agent_api.query import lookup_board_query_sql

        lr = lookup_board_query_sql("bad", path, project=local_project(tmp_path))
        assert lr.success is False
        assert any("doesnotexist" in e for e in lr.errors), lr.errors

    def test_validate_on_composed_query_reports_no_parse_error(
        self,
        tmp_path: Path,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import lookup_board_query_sql
        from dbt_charts.agent_api.validate_query import validate_query

        lr = lookup_board_query_sql(
            "wrapper", probe_board_path, project=local_project(tmp_path)
        )
        assert lr.success is True, lr.errors
        diagnostics = validate_query(lr.sql)
        # No WARN-PARSE-ERROR — the linter received valid SQL, not a Jinja template
        codes = {d.code for d in diagnostics}
        assert "WARN-PARSE-ERROR" not in codes, (
            f"validate returned a parse error on composed query: {diagnostics}"
        )

    def test_validate_with_var_override_binds_value(
        self,
        tmp_path: Path,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """--var n=9 should produce SQL with 9, not {{ n }}."""
        from dbt_charts.agent_api.query import lookup_board_query_sql

        lr = lookup_board_query_sql(
            "wrapper",
            probe_board_path,
            project=local_project(tmp_path),
            vars={"n": 9},
        )
        assert lr.success is True, lr.errors
        # The rendered SQL should not contain any Jinja tokens
        assert "{{" not in lr.sql
        assert "}}" not in lr.sql

    def test_lookup_plain_query_renders_variable(
        self,
        tmp_path: Path,
        probe_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Even a non-composed query's variables should be rendered."""
        from dbt_charts.agent_api.query import lookup_board_query_sql

        lr = lookup_board_query_sql(
            "plain", probe_board_path, project=local_project(tmp_path)
        )
        assert lr.success is True, lr.errors
        assert "{{" not in lr.sql


# ---------------------------------------------------------------------------
# Root cause 3: the *referenced* query's own SQL carries a template
# ---------------------------------------------------------------------------

_TEMPLATED_REF_BOARD = """\
title: probe
source: mem
variables:
  n:
    input: number
    default: 3
queries:
  base:
    sql: "SELECT 1 AS one, {{ n }} AS n"
  wrapper:
    sql: "SELECT one, n FROM {{ queries.base }}"
charts:
  k:
    query: wrapper
    type: kpi
    value: one
"""


@pytest.fixture
def templated_ref_board_path(tmp_path: Path) -> Path:
    path = tmp_path / "templated_ref.yml"
    path.write_text(_TEMPLATED_REF_BOARD)
    return path


class TestComposedQueryWithTemplatedReference:
    """The referenced query's own template must render, not just get inlined."""

    def test_composed_query_renders_referenced_querys_variable(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        templated_ref_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            templated_ref_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is True, result.errors
        assert result.data == [{"one": 1, "n": 3}]

    def test_composed_query_var_override_reaches_referenced_query(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        templated_ref_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            templated_ref_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
            vars={"n": 9},
        )
        assert result.success is True, result.errors
        assert result.data == [{"one": 1, "n": 9}]

    def test_lookup_board_query_sql_renders_referenced_querys_variable(
        self,
        tmp_path: Path,
        templated_ref_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import lookup_board_query_sql

        lr = lookup_board_query_sql(
            "wrapper", templated_ref_board_path, project=local_project(tmp_path)
        )
        assert lr.success is True, lr.errors
        # Parameterized, not merely absent: "{{" not in lr.sql alone can't tell
        # a rendered placeholder apart from a vanished template.
        assert "$1 AS n" in lr.sql


# ---------------------------------------------------------------------------
# Root cause 4: a referenced query's setup_sql must run before composition
# ---------------------------------------------------------------------------

_SETUP_SQL_BOARD = """\
title: probe
source: mem
queries:
  base:
    sql: SELECT 1 AS one
    setup_sql: "CREATE TEMP TABLE t AS SELECT 5 AS v"
  wrapper:
    sql: "SELECT v FROM t, {{ queries.base }}"
charts:
  k:
    query: wrapper
    type: kpi
    value: v
"""


@pytest.fixture
def setup_sql_board_path(tmp_path: Path) -> Path:
    path = tmp_path / "setup_sql.yml"
    path.write_text(_SETUP_SQL_BOARD)
    return path


class TestComposedQueryPropagatesSetupSql:
    """query_board must run a referenced query's setup_sql, same as execution.

    ``wrapper`` selects from ``t``, a temp table only ``base``'s setup_sql
    creates — resolve_query_references's dependency walk must pull that
    setup_sql onto ``wrapper`` for the DuckDB adapter to run it first.
    """

    def test_query_board_runs_referenced_querys_setup_sql(
        self,
        tmp_path: Path,
        probe_registry: AdapterRegistry,
        setup_sql_board_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "wrapper",
            setup_sql_board_path,
            local_project(tmp_path),
            adapter_registry=probe_registry,
        )
        assert result.success is True, result.errors
        assert result.data == [{"v": 5}]
