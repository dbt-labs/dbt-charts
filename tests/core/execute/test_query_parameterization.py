"""Tests for SQL injection safety on the executor + adapter path.

Coverage:
    - DuckDB adapter end-to-end: bound params reach _execute_duckdb unchanged
    - AdapterRegistry params passthrough
    - execute_query: malicious variable value is bound, never interpolated into SQL
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters.base import QueryResult

pytestmark = pytest.mark.usefixtures("typed_via_execute")


class TestAdapterParamsPassthrough:
    """Adapter and registry correctly forward pre-computed params."""

    def test_duckdb_adapter_uses_provided_params(
        self, local_project: "FilesystemProject"
    ):
        """DuckDB adapter passes pre-computed params to _execute_duckdb unchanged."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            data_dir=local_project(Path("/tmp")).data_path("."),
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        query = SqlQuery(sql="SELECT * FROM orders WHERE region = $1", source="test_db")

        with patch.object(adapter, "_execute_duckdb") as mock_duckdb:
            mock_duckdb.return_value = QueryResult(data=[], schema=literal_schema([]))

            adapter.execute(query, variables={}, params=["North"])

            call_args = mock_duckdb.call_args
            assert call_args[0][1] == ["North"]

    def test_adapter_registry_passes_params_through(
        self, local_project: "FilesystemProject"
    ):
        """AdapterRegistry forwards the params kwarg to the adapter's execute."""
        from unittest.mock import MagicMock

        from dbt_charts.core.execute.adapters import AdapterRegistry
        from dbt_charts.core.execute.source_resolver import SourceResolver

        mock_resolver = MagicMock(spec=SourceResolver)
        mock_resolver.resolve.return_value = None
        registry = AdapterRegistry(
            project=local_project(Path()), resolver=mock_resolver
        )

        mock_adapter = Mock()
        mock_adapter.supported_types = {"sql"}
        mock_adapter.can_execute.return_value = True
        mock_adapter.execute.return_value = QueryResult(
            data=[], schema=literal_schema([])
        )

        registry._adapters = [mock_adapter]
        registry._type_index = {"sql": [mock_adapter]}

        query = SqlQuery(sql="SELECT 1", source="db")

        registry.execute(query, variables={}, params=["test"])

        mock_adapter.execute.assert_called_once()
        call_args = mock_adapter.execute.call_args
        assert call_args[0][2] == ["test"]


class TestExecuteQueryParameterization:
    """execute_query routes injection-safe params through the DuckDB adapter."""

    def test_injection_prevented_via_duckdb_adapter(
        self, local_project: "FilesystemProject"
    ):
        """Malicious variable value arrives at _execute_duckdb as a bound param.

        If render_parameterized were broken to do string interpolation instead,
        the injected SQL fragment would appear in the sql argument and this test
        would fail.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

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

        # Find the DuckDB adapter to patch _execute_duckdb
        duckdb_adapter = next(
            (a for a in registry._adapters if isinstance(a, DuckDBAdapter)),
            None,
        )
        assert duckdb_adapter is not None

        captured_calls: list[tuple[str, list]] = []

        def capture_duckdb(sql: str, params: list, *args, **kwargs) -> QueryResult:
            captured_calls.append((sql, list(params or [])))
            return QueryResult(data=[], schema=literal_schema([]))

        malicious = "'; DROP TABLE users; --"

        with patch.object(
            duckdb_adapter, "_execute_duckdb", side_effect=capture_duckdb
        ):
            executor = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                use_cache=False,
            )
            executor.execute_query("users", {"name": malicious})

        assert len(captured_calls) == 1, "Expected exactly one _execute_duckdb call"
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
        # reroute these queries to DbtAdapter, which ignores params.
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
