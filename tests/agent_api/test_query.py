"""Tests for dbt_charts.agent_api.query — typed return contract + query_board verb."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry
from dbt_charts.core.project import Project


@pytest.fixture
def registry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> AdapterRegistry:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    return build_adapter_registry(local_project(tmp_path), read_only=False)


def _write_board(tmp_path: Path, yaml_content: str, name: str = "test.yml") -> Path:
    path = tmp_path / name
    path.write_text(yaml_content)
    return path


SIMPLE_BOARD = """
source: db
queries:
  revenue:
    sql: "select 1 as a, 2 as b"
charts:
  c:
    query: revenue
    type: table
rows:
  - c
"""

# ---------------------------------------------------------------------------
# SqlQuery model — lenient_variables field
# ---------------------------------------------------------------------------


def test_sql_query_lenient_variables_field() -> None:
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    assert (
        SqlQuery(
            sql="SELECT 1", lenient_variables=True, source="test_db"
        ).lenient_variables
        is True
    )
    assert SqlQuery(sql="SELECT 1", source="test_db").lenient_variables is False


# ---------------------------------------------------------------------------
# execute_query — typed Pydantic return
# ---------------------------------------------------------------------------


class TestExecuteQueryLenientVariables:
    """lenient_variables=True lets undefined Jinja variables produce 1=1 fallback."""

    def test_lenient_undefined_var_does_not_raise(
        self, tmp_path: Path, registry: AdapterRegistry
    ) -> None:
        from dbt_charts.agent_api.query import execute_query

        result = execute_query(
            sql="SELECT 1 WHERE {{ filter('city', city) }}",
            variables={},
            source="db",
            adapter_registry=registry,
            lenient_variables=True,
        )
        assert result.success is True

    def test_strict_undefined_var_returns_error(
        self, tmp_path: Path, registry: AdapterRegistry
    ) -> None:
        from dbt_charts.agent_api.query import execute_query

        result = execute_query(
            sql="SELECT 1 WHERE {{ filter('city', city) }}",
            variables={},
            source="db",
            adapter_registry=registry,
        )
        assert result.success is False


class TestExecuteQuerySourcelessHardError:
    """execute_query hard-errors on source=None — no default-DuckDB fallback."""

    def test_sourceless_query_fails_even_with_a_configured_source(
        self, registry: AdapterRegistry
    ) -> None:
        """A registry with a real named source ("db") must not let a sourceless
        query silently fall through to the engine's scratch DuckDB."""
        from dbt_charts.agent_api.query import execute_query

        result = execute_query(sql="SELECT 1 as x", adapter_registry=registry)
        assert result.success is False
        assert result.errors
        assert "names no source" in result.errors[0]

    def test_named_source_still_works(self, registry: AdapterRegistry) -> None:
        from dbt_charts.agent_api.query import execute_query

        result = execute_query(
            sql="SELECT 1 as x", source="db", adapter_registry=registry
        )
        assert result.success is True
        assert result.data == [{"x": 1}]


class TestExecuteQueryTypedReturn:
    """execute_query returns a typed Pydantic model, not a bare dict."""

    def test_error_result_is_typed_with_expected_attributes(
        self, empty_registry: AdapterRegistry
    ) -> None:
        from dbt_charts.agent_api.query import ExecuteQueryResult, execute_query

        # Empty registry has no adapters — execute will fail, but return is still typed.
        result = execute_query(sql="SELECT 1", adapter_registry=empty_registry)
        assert isinstance(result, ExecuteQueryResult)
        assert result.success is False
        assert isinstance(result.errors, list)
        assert isinstance(result.columns, list)
        assert isinstance(result.data, list)
        assert result.row_count == 0
        assert result.truncated is False

    def test_model_dump_produces_wire_shape(
        self, empty_registry: AdapterRegistry
    ) -> None:
        from dbt_charts.agent_api.query import execute_query

        # A re-aggregation query so diagnostics is non-empty — this is the JSON wire
        # boundary (print_json_result's model_dump_json(exclude_none=True)),
        # so a query with zero diagnostics would never exercise the recursive
        # null-dropping into the typed diagnostics list this test pins.
        result = execute_query(
            sql="SELECT AVG(t) FROM (SELECT r, SUM(x) AS t FROM a GROUP BY r) s",
            source="db",
            adapter_registry=empty_registry,
        )
        assert result.diagnostics

        wire = json.loads(result.model_dump_json(exclude_none=True))
        for key in ("success", "columns", "data", "errors", "row_count", "truncated"):
            assert key in wire
        assert "error" not in wire

        # confidence is unset on a re-aggregation finding — exclude_none drops
        # the key entirely rather than serializing null; detail/recommendation,
        # which the validator does populate, still show.
        for diag in wire["diagnostics"]:
            assert None not in diag.values()
            assert "confidence" not in diag
            assert diag["detail"]

    def test_diagnostics_are_typed_query_diagnostic_not_dict(
        self, empty_registry: AdapterRegistry
    ) -> None:
        """ExecuteQueryResult.diagnostics carries QueryDiagnostic objects, not
        pre-flattened dicts — agent_api return types are never dict[str, Any]."""
        from dbt_charts.agent_api.query import execute_query
        from dbt_charts.agent_api.validate_query import QueryDiagnostic

        result = execute_query(
            sql="SELECT AVG(t) FROM (SELECT r, SUM(x) AS t FROM a GROUP BY r) s",
            source="db",
            adapter_registry=empty_registry,
        )
        assert result.diagnostics
        for d in result.diagnostics:
            assert isinstance(d, QueryDiagnostic)
            assert d.code


# ---------------------------------------------------------------------------
# query_board — board-aware query verb
# ---------------------------------------------------------------------------


class TestQueryBoardReturnsData:
    def test_success_result_has_typed_data_and_attributes(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import QueryBoardResult, query_board

        path = _write_board(tmp_path, SIMPLE_BOARD)
        result = query_board(
            "revenue", path, local_project(tmp_path), adapter_registry=registry
        )
        assert isinstance(result, QueryBoardResult)
        assert result.success is True
        # Data facets
        assert result.columns == ["a", "b"]
        assert len(result.data) == 1
        assert result.row_count == 1
        # Identity / contract facets
        assert result.name == "revenue"
        assert result.path == PurePosixPath("test.yml")
        assert result.query_type == "sql"
        assert result.sql is not None
        assert result.errors == []
        assert result.available_queries == []


class TestQueryBoardUnknownName:
    def test_unknown_name_surfaces_failure_with_available_queries(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, SIMPLE_BOARD)
        result = query_board(
            "missing", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is False
        assert any("missing" in e for e in result.errors)
        assert result.available_queries == ["revenue"]


class TestQueryBoardCompileErrors:
    def test_compile_error_surfaces_failure_with_errors(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        # Invalid YAML (unclosed bracket) triggers a parse/compile error
        path = _write_board(tmp_path, "queries:\n  bad: [\n")
        result = query_board(
            "bad", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is False
        assert len(result.errors) > 0

    def test_missing_file_returns_failure(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "revenue",
            tmp_path / "nonexistent.yml",
            local_project(tmp_path),
            adapter_registry=registry,
        )
        assert result.success is False
        assert any("not found" in e.lower() for e in result.errors)


class TestQueryFaceTruncation:
    def test_truncates_at_limit(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = """
source: db
queries:
  many:
    sql: |
      SELECT generate_series AS n FROM generate_series(1, 30)
charts:
  c:
    query: many
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "many",
            path,
            local_project(tmp_path),
            adapter_registry=registry,
            limit=5,
        )
        assert result.success is True
        assert len(result.data) == 5
        assert result.truncated is True
        assert result.row_count == 5

    def test_no_truncation_when_under_limit(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, SIMPLE_BOARD)
        result = query_board(
            "revenue",
            path,
            local_project(tmp_path),
            adapter_registry=registry,
            limit=20,
        )
        assert result.success is True
        assert result.truncated is False

    def test_limit_clamps_at_max(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import MAX_QUERY_LIMIT, query_board

        board = f"""
source: db
queries:
  many:
    sql: |
      SELECT generate_series AS n FROM generate_series(1, {MAX_QUERY_LIMIT + 500})
charts:
  c:
    query: many
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "many",
            path,
            local_project(tmp_path),
            adapter_registry=registry,
            limit=100000,
        )
        assert result.success is True
        assert result.row_count == MAX_QUERY_LIMIT
        assert result.truncated is True


class TestQueryBoardVarOverrides:
    def test_applies_var_overrides(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = """
source: db
variables:
  country:
    input: text
    default: US
queries:
  filtered:
    sql: "select '{{ country }}' as country"
charts:
  c:
    query: filtered
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "filtered",
            path,
            local_project(tmp_path),
            vars={"country": "FR"},
            adapter_registry=registry,
        )
        assert result.success is True
        assert result.data[0]["country"] == "FR"


_DATE_RANGE_BOARD = """
source: db
variables:
  date_range:
    input: daterange
queries:
  by_day:
    sql: |
      select count(*) as n
      from (select date '2024-01-05' as created_at) t
      where {{ filter_date_range('created_at', date_range) }}
charts:
  c:
    query: by_day
    type: table
rows:
  - c
"""


class TestQueryBoardUnboundVariables:
    """A variable declared without ``default:`` has nothing to bind unless the
    caller passes it; the error must say so instead of a raw Jinja failure."""

    def test_error_names_the_variable_its_input_and_what_to_pass(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, _DATE_RANGE_BOARD)
        result = query_board(
            "by_day", path, local_project(tmp_path), adapter_registry=registry
        )

        assert result.success is False
        [error] = result.errors
        assert "Undefined variable" not in error
        assert "'date_range'" in error
        assert "daterange" in error
        assert "default" in error
        assert '["2024-01-01", "2024-01-31"]' in error
        assert result.sql is not None

    def test_passing_the_value_the_error_asks_for_runs_the_query(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, _DATE_RANGE_BOARD)
        result = query_board(
            "by_day",
            path,
            local_project(tmp_path),
            vars={"date_range": '["2024-01-01", "2024-01-31"]'},
            adapter_registry=registry,
        )

        assert result.success is True
        assert result.data == [{"n": 1}]

    def test_a_declared_default_still_binds(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = _DATE_RANGE_BOARD.replace(
            "    input: daterange\n",
            "    input: daterange\n    default: ['2024-01-01', '2024-01-31']\n",
        )
        path = _write_board(tmp_path, board)
        result = query_board(
            "by_day", path, local_project(tmp_path), adapter_registry=registry
        )

        assert result.success is True
        assert result.data == [{"n": 1}]


class TestQueryBoardPathSecurity:
    def test_path_escape_rejected(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        result = query_board(
            "revenue",
            Path("../../etc/passwd"),
            local_project(tmp_path),
            adapter_registry=registry,
        )
        assert result.success is False
        assert len(result.errors) > 0
        # The rejected input is echoed back as the raw path, not resolved to disk.
        assert result.path == PurePosixPath("../../etc/passwd")


class TestQueryBoardDisplayPath:
    def test_missing_file_reports_relpath_not_project_root(
        self,
        empty_registry: AdapterRegistry,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The not-found path must carry the ProjectPath relpath identity, never
        `project.root` — which for a non-filesystem host (Cloud) is the worker's
        cwd, not a real project directory, so joining against it produces a bogus
        absolute path pointing nowhere."""
        from dbt_charts.agent_api.query import query_board

        bogus_root = Path("/nonexistent-bogus-root/should-not-appear")
        project = in_memory_project(bogus_root, {})
        result = query_board(
            "revenue",
            Path("missing.yml"),
            project,
            adapter_registry=empty_registry,
        )
        assert result.success is False
        assert result.path == PurePosixPath("missing.yml")
        assert str(bogus_root) not in result.errors[0]


class TestQueryBoardNonFilesystemProject:
    def test_non_filesystem_project_is_not_refused(
        self,
        tmp_path: Path,
        empty_registry: AdapterRegistry,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        # The board is only in the InMemoryProject's store, never written to
        # tmp_path on real disk — a raw `Path.exists()` probe would see nothing
        # there and report "File not found", masking the guard removal. Pin
        # both: no filesystem-only refusal, AND no not-found fallback, so a
        # revert of the `path_for_fspath(...).exists()` swap fails this test.
        project = in_memory_project(tmp_path, {"test.yml": SIMPLE_BOARD})
        result = query_board(
            "revenue", Path("test.yml"), project, adapter_registry=empty_registry
        )
        assert not any(
            "requires a local filesystem project" in e for e in result.errors
        )
        assert not any("File not found" in e for e in result.errors)
        # The run reached execution: the board compiled from the store (proving
        # the existence check resolved against InMemoryProject, not disk) and
        # failed only because empty_registry has no source configured.
        assert result.success is False
        assert any("source" in e.lower() for e in result.errors)


class TestQueryBoardNonSqlQuery:
    def test_values_query_returns_data(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = """
queries:
  inline:
    type: values
    rows:
      - {a: 1, b: 2}
      - {a: 3, b: 4}
charts:
  c:
    query: inline
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "inline", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is True
        assert result.query_type == "values"
        assert result.sql is None
        assert len(result.data) == 2


class TestQueryBoardNote:
    """A named board query's authored `notes:` reaches the tool result.

    The note is already authored in YAML and already survives compile
    (`normalized.SqlQuery.notes`); returning it is what lets a caller
    label the call with what it is for instead of its identifier.
    """

    def test_authored_notes_is_returned(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = """
source: db
queries:
  leads_by_industry:
    notes: Leads grouped by industry
    sql: "select 1 as a"
charts:
  c:
    query: leads_by_industry
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "leads_by_industry",
            path,
            local_project(tmp_path),
            adapter_registry=registry,
        )
        assert result.success is True
        assert result.notes == "Leads grouped by industry"

    def test_unnoted_query_returns_none(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, SIMPLE_BOARD)
        result = query_board(
            "revenue", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is True
        assert result.notes is None


class TestQueryBoardFailureCarriesSql:
    """A failure past query lookup still reports the SQL it tried to run.

    Without it an execution error shows a reason with nothing to read it
    against — a Jinja or warehouse failure names a symbol the caller cannot
    locate without the query text. Failures raised before the query is looked
    up (unresolvable path, compile error, unknown name) genuinely have no SQL,
    and must not invent one.
    """

    def test_execution_failure_reports_sql(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        board = """
source: db
queries:
  leads:
    sql: "select industry from no_such_table"
charts:
  c:
    query: leads
    type: table
rows:
  - c
"""
        path = _write_board(tmp_path, board)
        result = query_board(
            "leads", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is False
        assert result.errors
        assert result.sql == "select industry from no_such_table"

    def test_failure_before_lookup_has_no_sql(
        self,
        tmp_path: Path,
        registry: AdapterRegistry,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        from dbt_charts.agent_api.query import query_board

        path = _write_board(tmp_path, SIMPLE_BOARD)
        result = query_board(
            "no_such_query", path, local_project(tmp_path), adapter_registry=registry
        )
        assert result.success is False
        assert result.sql is None


class TestExecuteQueryDescription:
    """Ad-hoc SQL has no author to describe it, so the caller may say what the
    query is for. Optional — a query with no stated purpose is still valid."""

    def test_description_is_accepted(self) -> None:
        from dbt_charts.agent_api.query import ExecuteQueryArgs

        args = ExecuteQueryArgs.model_validate(
            {"sql": "select 1", "description": "Counting leads by industry"}
        )
        assert args.description == "Counting leads by industry"

    def test_description_defaults_to_none(self) -> None:
        from dbt_charts.agent_api.query import ExecuteQueryArgs

        assert ExecuteQueryArgs.model_validate({"sql": "select 1"}).description is None


class TestVariableBinding:
    """VariableBinding and variables_to_dict: wire shape + duplicate-name guard."""

    def test_variables_to_dict_basic(self) -> None:
        from dbt_charts.agent_api.query import VariableBinding, variables_to_dict

        bindings = [
            VariableBinding(name="region", value="US"),
            VariableBinding(name="year", value=2024),
            VariableBinding(name="active", value=True),
        ]
        result = variables_to_dict(bindings)
        assert result == {"region": "US", "year": 2024, "active": True}

    def test_variables_to_dict_duplicate_raises(self) -> None:
        from dbt_charts.agent_api.query import VariableBinding, variables_to_dict

        bindings = [
            VariableBinding(name="region", value="US"),
            VariableBinding(name="region", value="EU"),
        ]
        with pytest.raises(ValueError, match="region"):
            variables_to_dict(bindings)

    def test_variables_to_dict_empty_returns_empty(self) -> None:
        from dbt_charts.agent_api.query import variables_to_dict

        assert variables_to_dict([]) == {}

    def test_execute_query_args_accepts_variable_bindings(self) -> None:
        from dbt_charts.agent_api.query import ExecuteQueryArgs

        args = ExecuteQueryArgs.model_validate(
            {
                "sql": "SELECT * FROM t WHERE region = {{ region }}",
                "variables": [{"name": "region", "value": "US"}],
            }
        )
        assert args.variables is not None
        assert len(args.variables) == 1
        assert args.variables[0].name == "region"
        assert args.variables[0].value == "US"

    def test_execute_query_args_duplicate_variable_names_rejected(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.agent_api.query import ExecuteQueryArgs

        with pytest.raises(ValidationError, match="region"):
            ExecuteQueryArgs.model_validate(
                {
                    "sql": "SELECT 1",
                    "variables": [
                        {"name": "region", "value": "US"},
                        {"name": "region", "value": "EU"},
                    ],
                }
            )

    def test_execute_query_args_accept_a_named_board_query(self) -> None:
        from dbt_charts.agent_api.query import ExecuteQueryArgs

        args = ExecuteQueryArgs.model_validate(
            {
                "board": "charts/rev.yml",
                "query": "revenue",
                "variables": [{"name": "year", "value": 2024}],
            }
        )
        assert args.sql is None
        assert args.query == "revenue"
        assert args.variables is not None and args.variables[0].value == 2024
