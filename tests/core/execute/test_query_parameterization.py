"""Tests for SQL injection safety on the executor + adapter path.

Coverage:
    - DuckDB adapter end-to-end: bound params reach the DuckDB backend unchanged
    - AdapterRegistry params passthrough
    - execute_query: malicious variable value is bound, never interpolated into SQL
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import nanoarrow as na
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.arrow_ingest import table
from dbt_charts.core.execute.compose import ResolvedQuery

pytestmark = pytest.mark.usefixtures("typed_via_execute")


class TestAdapterParamsPassthrough:
    """Adapter and registry correctly forward pre-computed params."""

    def test_duckdb_backend_receives_provided_params(
        self, local_project: FilesystemProject
    ):
        """Pre-computed params reach the DuckDB backend unchanged."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        from ..._backend_runner import DuckDBRunner

        adapter = DuckDBRunner(
            data_dir=local_project(Path("/tmp")).data_path("."),
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        query = SqlQuery(sql="SELECT * FROM orders WHERE region = $1", source="test_db")

        with patch.object(
            adapter.backend, "execute", return_value=na.Array(table([], 0))
        ) as mock_execute:
            adapter.execute(query, variables={}, params=["North"])

        assert mock_execute.call_args[0][1].params == ("North",)

    def test_adapter_registry_passes_params_through(
        self, local_project: FilesystemProject
    ):
        """AdapterRegistry hands pre-rendered params to the backend as bindings."""
        from unittest.mock import MagicMock

        import nanoarrow as na
        import pyarrow as pa

        from dbt_charts.core.compile.models.source import DuckDBSourceConfig
        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.execute.backends.base import BackendRegistry
        from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

        backend = MagicMock()
        backend.param_style = INLINE_PLACEHOLDERS
        backend.execute.return_value = na.Array(pa.table({"n": [1]}))
        registry = AdapterRegistry(
            project=local_project(Path()),
            backends=BackendRegistry({"duckdb": backend}),
        )

        registry.execute(
            SqlQuery(sql="SELECT 1", source="db"),
            variables={},
            params=["test"],
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        backend.execute.assert_called_once()
        assert backend.execute.call_args[0][1].params == ("test",)


class TestExecuteQueryParameterization:
    """execute_query routes injection-safe params through the DuckDB adapter."""

    def test_injection_prevented_via_duckdb_adapter(
        self, local_project: FilesystemProject
    ):
        """Malicious variable value arrives at the DuckDB backend as a bound param.

        If render_parameterized were broken to do string interpolation instead,
        the injected SQL fragment would appear in the sql argument and this test
        would fail.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor

        yaml_content = """\
title: Test
variables:
  name:
    input: text
    default: Alice
queries:
  users:
    sql: SELECT * FROM users WHERE name = '{{ name }}'
    source: duckdb
charts:
  c:
    query: users
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors

        from dbt_charts.core.compile.config import ProjectSourcesConfig

        project = local_project(Path.cwd())
        project.__dict__["sources"] = ProjectSourcesConfig(
            sources={"duckdb": {"type": "duckdb", "path": ":memory:"}}
        )

        from dbt_charts.core.execute.adapters import build_adapter_registry

        registry = build_adapter_registry(project)

        backend = registry._backends.for_source(  # noqa: SLF001
            DuckDBSourceConfig(type="duckdb")
        )

        captured_calls: list[tuple[str, list]] = []

        def capture_duckdb(source: object, resolved: ResolvedQuery) -> na.Array:
            captured_calls.append((resolved.sql, list(resolved.params)))
            return na.Array(table([], 0))

        malicious = "'; DROP TABLE users; --"

        with patch.object(backend, "execute", side_effect=capture_duckdb):
            executor = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                use_cache=False,
            )
            executor.execute_query("users", {"name": malicious})

        assert len(captured_calls) == 1, "Expected exactly one DuckDB backend call"
        sql_sent, params_sent = captured_calls[0]

        # Injection must NOT appear in the SQL text
        assert "DROP TABLE" not in sql_sent
        assert "--" not in sql_sent
        # Placeholder must be present
        assert "$1" in sql_sent
        # Malicious value must be in params (bound safely)
        assert malicious in params_sent


class TestPlaceholderShapedLiteralsThroughExecutor:
    """Board renders route through the registry composition point (every board
    with `queries:`), which pre-renders SQL and hands the adapter params. An
    authored literal shaped like the warehouse's own placeholder must survive
    that path — the composition render has to use the placeholder style the
    target adapter declares, not the warehouse's own syntax."""

    def _driver_sql_for(
        self,
        local_project,
        tmp_path: Path,
        yaml_content: str,
        query_name: str,
        variables: dict,
    ):
        """Compile a board, execute one query via Executor, return (result, driver SQL)."""
        from unittest.mock import MagicMock

        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry

        result = compile(yaml_content)
        assert result.success, result.errors

        # tmp_path, not CWD: a dbt_project.yml in the CWD would silently
        # reroute these queries to the dbt target.
        project = local_project(tmp_path)
        project.__dict__["sources"] = ProjectSourcesConfig(
            sources={
                "pg": {
                    "type": "postgres",
                    "host": "h",
                    "dbname": "db",
                    "user": "u",
                    "password": "p",
                }
            }
        )
        registry = build_adapter_registry(project)

        captured: list[str] = []
        mock_table = MagicMock()
        mock_table.column_names = ["tier"]
        mock_table.rows = [("v",)]
        mock_adapter = MagicMock()
        mock_adapter.execute.side_effect = lambda sql, **kw: (
            captured.append(sql),
            (None, mock_table),
        )[1]

        try:
            with patch(
                "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
                return_value=mock_adapter,
            ):
                executor = Executor(
                    result.board,
                    adapter_registry=registry,
                    query_registry=result.query_registry,
                    use_cache=False,
                )
                exec_result = executor.execute_query(query_name, variables)
        finally:
            registry.close()

        # execute_query returns rows and raises QueryError on failure.
        assert isinstance(exec_result, list)
        # Setup statements (session timeout) may precede the data query.
        return next((s for s in reversed(captured) if "SELECT" in s.upper()), "")

    def test_dollar_literal_survives_a_board_render(self, local_project, tmp_path):
        sql = self._driver_sql_for(
            local_project,
            tmp_path,
            """\
title: Test
variables:
  region:
    input: text
    default: North
queries:
  tiers:
    sql: SELECT '$1,000+' AS tier FROM orders WHERE region = '{{ region }}'
    source: pg
charts:
  c:
    query: tiers
    type: table
rows:
  - c
""",
            "tiers",
            {"region": "North"},
        )
        assert "'$1,000+'" in sql
        assert "region = 'North'" in sql

    def test_string_filter_on_variable_fails_loud(self, local_project, tmp_path):
        """`{{ region | upper }}` would corrupt the internal placeholder token
        and silently drop the parameter — it must fail loudly at render,
        naming the variable, never ship a mangled token to the driver."""
        from dbt_charts.core.diagnostics.execution import QueryError

        with pytest.raises(QueryError, match="region"):
            self._driver_sql_for(
                local_project,
                tmp_path,
                """\
title: Test
variables:
  region:
    input: text
    default: North
queries:
  tiers:
    sql: SELECT * FROM orders WHERE upper(region) = '{{ region | upper }}'
    source: pg
charts:
  c:
    query: tiers
    type: table
rows:
  - c
""",
                "tiers",
                {"region": "north"},
            )

    def test_composed_query_reference_still_expands(self, local_project, tmp_path):
        sql = self._driver_sql_for(
            local_project,
            tmp_path,
            """\
title: Test
variables:
  region:
    input: text
    default: North
queries:
  base:
    sql: SELECT region, revenue FROM orders
    source: pg
  top:
    sql: >-
      SELECT '$1,000+' AS tier FROM ({{ queries.base }}) b
      WHERE region = '{{ region }}'
    source: pg
charts:
  c:
    query: top
    type: table
rows:
  - c
""",
            "top",
            {"region": "North"},
        )
        assert "FROM orders" in sql  # the reference expanded
        assert "'$1,000+'" in sql
        assert "region = 'North'" in sql
