"""Regression tests for DbtAdapter routing and error-wrapping behavior."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.project import Project


def _dbt(
    dbt_project_path: Path,
    local_project: Callable[..., FilesystemProject],
    target_name: str = "dev",
    **kwargs: object,
) -> DbtAdapter:
    """Create a DbtAdapter with required args."""
    kwargs.setdefault("project", local_project(dbt_project_path))
    return DbtAdapter(  # type: ignore[arg-type]
        dbt_project_path=dbt_project_path, target_name=target_name, **kwargs
    )


class TestCanExecutePure:
    """_can_execute must be a cheap predicate — no construction, no I/O.

    DbtAdapter claims only source-less SQL that uses dbt jinja ({{ ref() }} /
    {{ source() }}); a resolved source routes on its own type, and plain
    source-less SQL falls to DuckDB as the default engine.
    """

    def test_resolved_source_returns_false(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A resolved source is another adapter's territory, jinja or not."""
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        adapter = _dbt(
            dbt_project_path=Path("/nonexistent"), local_project=local_project
        )
        query = SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source="x")
        assert adapter._can_execute(query, DuckDBSourceConfig(type="duckdb")) is False

    def test_sourceless_plain_sql_returns_false(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Plain source-less SQL falls to DuckDB, not DbtAdapter."""
        adapter = _dbt(
            dbt_project_path=Path("/nonexistent"), local_project=local_project
        )
        assert (
            adapter._can_execute(SqlQuery(sql="SELECT 1", source=None), None) is False
        )

    def test_sourceless_ref_jinja_returns_true(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Source-less `{{ ref() }}` SQL is DbtAdapter's territory."""
        adapter = _dbt(
            dbt_project_path=Path("/nonexistent"), local_project=local_project
        )
        query = SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source=None)
        assert adapter._can_execute(query, None) is True

    def test_sourceless_source_jinja_returns_true(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Source-less `{{ source() }}` SQL is DbtAdapter's territory too."""
        adapter = _dbt(
            dbt_project_path=Path("/nonexistent"), local_project=local_project
        )
        query = SqlQuery(sql="SELECT * FROM {{ source('raw', 'events') }}", source=None)
        assert adapter._can_execute(query, None) is True

    def test_can_execute_does_not_read_filesystem(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """_can_execute must not read the manifest as a side-effect."""
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        query = SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source=None)
        resolve_called = []

        original = adapter._dbt_refs.resolve

        def _spy(sql: str) -> tuple[str, list[Any]]:
            resolve_called.append(True)
            return original(sql)

        adapter._dbt_refs.resolve = _spy  # type: ignore[method-assign]
        adapter._can_execute(query, None)
        assert not resolve_called, "the manifest must not be read during _can_execute"


class TestExecuteReturnsCleanErrorOnMisconfig:
    """Regression: manifest exists but profiles.yml is missing must not crash."""

    def test_missing_profiles_yml_returns_error_result(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """DbtAdapter.execute() on a project with manifest but no profiles.yml
        must return a QueryResult with an error message, not raise an exception."""
        from dbt_charts.core.execute.adapters.base import QueryResult

        # Write a minimal dbt_project.yml so profile resolution starts
        (tmp_path / "dbt_project.yml").write_text("name: test\nprofile: myprofile\n")
        # Deliberately omit profiles.yml so construction raises FileNotFoundError

        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        query = SqlQuery(sql="SELECT 1", source="my_named_source")
        result = adapter.execute(query)

        assert isinstance(result, QueryResult)
        assert result.error is not None
        assert result.data == []
        # Error message must mention the missing config — not an AttributeError
        error_lower = result.error.lower()
        assert (
            "profile" in error_lower
            or "profiles" in error_lower
            or "not found" in error_lower
        )

    def test_missing_dbt_project_yml_returns_error_result(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """DbtAdapter.execute() with no dbt_project.yml returns QueryResult.error,
        not FileNotFoundError leaking to the caller."""
        from dbt_charts.core.execute.adapters.base import QueryResult

        # Empty project dir — no dbt_project.yml, no profiles.yml
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        query = SqlQuery(sql="SELECT 1", source="my_named_source")
        result = adapter.execute(query)

        assert isinstance(result, QueryResult)
        assert result.error is not None

    def test_manifest_only_no_profiles_yml_returns_error_with_profiles_message(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """M3 regression: manifest+dbt_project.yml present, profiles.yml absent.

        Previously, manifest-only CI environments could resolve {{ ref() }} via
        SqlAdapter. The new design requires profiles.yml when dbt-jinja is present.
        The error result must mention 'profiles.yml' (not crash with AttributeError).
        """
        from dbt_charts.core.execute.adapters.base import QueryResult

        # Create manifest and dbt_project.yml — no profiles.yml
        (tmp_path / "dbt_project.yml").write_text("name: test\nprofile: myprofile\n")
        manifest = {
            "nodes": {
                "model.myproject.foo": {
                    "relation_name": "mydb.myschema.foo",
                    "schema": "myschema",
                    "alias": "foo",
                    "database": "mydb",
                }
            },
            "sources": {},
        }
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text(json.dumps(manifest))

        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        query = SqlQuery(sql="SELECT * FROM {{ ref('foo') }}", source="my_named_source")
        result = adapter.execute(query)

        assert isinstance(result, QueryResult)
        assert result.error is not None
        assert result.data == []
        # Must mention profiles.yml — makes the new contract explicit in the error
        assert "profiles.yml" in result.error or "profiles" in result.error.lower()


class TestReadTargetDict:
    """L1: available-profile filter uses exact 'config' match, not startswith."""

    def test_profile_named_config_prefix_is_listed_as_available(
        self, tmp_path: Path
    ) -> None:
        """A real profile named 'config_dev' must appear in the error's available list.

        The old `startswith('config')` filter hid such profiles — exact != 'config'
        is the fix.
        """
        from dbt_charts.core.execute.adapters.dbt_adapter import _read_target_dict

        (tmp_path / "profiles.yml").write_text(
            "config_dev:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            "      path: ':memory:'\n"
        )

        with pytest.raises(ValueError, match="config_dev"):
            _read_target_dict(tmp_path, "nonexistent", "dev")


class TestReadProfilesYml:
    """_read_profiles_yml must reject non-mapping content, not silently treat as {}."""

    def test_empty_profiles_yml_raises_value_error(self, tmp_path: Path) -> None:
        """An empty profiles.yml (safe_load → None) must raise ValueError, not return {}."""
        from dbt_charts.core.execute.adapters.dbt_adapter import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text("")
        with pytest.raises(ValueError, match="did not parse to a mapping"):
            _read_profiles_yml(tmp_path)

    def test_non_mapping_profiles_yml_raises_value_error(self, tmp_path: Path) -> None:
        """A profiles.yml containing only a list raises ValueError."""
        from dbt_charts.core.execute.adapters.dbt_adapter import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="did not parse to a mapping"):
            _read_profiles_yml(tmp_path)

    def test_valid_profiles_yml_returns_dict(self, tmp_path: Path) -> None:
        """A well-formed profiles.yml returns a dict."""
        from dbt_charts.core.execute.adapters.dbt_adapter import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text(
            "myprofile:\n  outputs:\n    dev:\n      type: duckdb\n"
        )
        result = _read_profiles_yml(tmp_path)
        assert isinstance(result, dict)
        assert "myprofile" in result


class TestRuntimeMutatingSqlGuard:
    """DbtAdapter must validate post-`{{ ref() }}` SQL before adapter.execute().
    The validation must see the resolved SQL — a custom dbt macro that resolves
    to DROP at runtime is the documented compile-time gap this guard closes.
    """

    def test_resolved_drop_rejected_before_adapter_execute(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A query that resolves to DROP TABLE after dbt-jinja resolution must
        be refused before adapter.execute() sees it. We stub `_resolve_dbt_sql`
        so the test doesn't need a real dbt manifest.
        """
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        with patch.object(adapter, "_resolve_dbt_sql", return_value="DROP TABLE x"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('innocent') }}", source="my_named_source"
            )
            result = adapter._execute(query)
        assert result.error is not None
        assert "outside the read-only SQL allowlist" in result.error
        adapter._adapter.execute.assert_not_called()

    def test_resolved_select_passes(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A query that resolves to a clean SELECT after dbt-jinja resolution
        must reach adapter.execute() unmodified — the guard adds no friction
        for legitimate queries.
        """
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        mock_table = MagicMock()
        mock_table.column_names = ["a"]
        mock_table.rows = [(1,)]
        adapter._adapter.execute.return_value = (None, mock_table)
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1 AS a"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('clean') }}", source="my_named_source"
            )
            result = adapter._execute(query)
        assert result.error is None
        adapter._adapter.execute.assert_called_once()


class TestDbtAdapterMaxRowsCeiling:
    """The execution.max_rows ceiling bounds the driver's own fetch — the
    limit= kwarg passed to adapter.execute() — never the SQL text.
    """

    def test_ceiling_binds_limit_kwarg_and_truncates(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        mock_table = MagicMock()
        mock_table.column_names = ["a"]
        # Simulates the driver's own fetchmany(limit=3) coming back with all
        # 3 requested rows — i.e. more rows existed than the ceiling allows.
        mock_table.rows = [(i,) for i in range(3)]
        adapter._adapter.execute.return_value = (None, mock_table)
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT a FROM t"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('t') }}", source="my_named_source"
            )
            result = adapter._execute(query)

        sent_sql = adapter._adapter.execute.call_args[0][0]
        sent_kwargs = adapter._adapter.execute.call_args[1]
        assert sent_sql == "SELECT a FROM t"
        assert sent_kwargs["limit"] == 3
        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason == "max_rows"

    def test_no_truncation_when_under_ceiling(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1000")
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        mock_table = MagicMock()
        mock_table.column_names = ["a"]
        mock_table.rows = [(1,)]
        adapter._adapter.execute.return_value = (None, mock_table)
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT 1 AS a"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('clean') }}", source="my_named_source"
            )
            result = adapter._execute(query)

        sent_kwargs = adapter._adapter.execute.call_args[1]
        assert sent_kwargs["limit"] == 1001
        assert result.error is None
        assert result.truncated_reason is None

    def test_author_limit_below_ceiling_passed_as_exact_limit_kwarg(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """When the author's own Query.limit binds (not the ceiling), the
        driver is asked for exactly that many rows — no over-fetch, no
        truncation flag, and no SQL rewrite of any kind."""
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        mock_table = MagicMock()
        mock_table.column_names = ["a"]
        mock_table.rows = [(i,) for i in range(2)]
        adapter._adapter.execute.return_value = (None, mock_table)
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT a FROM t"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('t') }}", source="my_named_source"
            )
            query.limit = 2
            result = adapter._execute(query)

        sent_sql = adapter._adapter.execute.call_args[0][0]
        sent_kwargs = adapter._adapter.execute.call_args[1]
        assert sent_sql == "SELECT a FROM t"
        assert sent_kwargs["limit"] == 2
        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason is None

    def test_spark_dialect_never_receives_a_limit_kwarg(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dbt-spark's Hive/ODBC cursor wrappers implement fetchall but not
        fetchmany — passing limit= there raises AttributeError inside
        dbt-adapters' get_result_from_cursor. The ceiling still applies via
        the post-fetch slice, just without a driver-level fetch bound."""
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")
        adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
        adapter._adapter = MagicMock()
        adapter._dialect = "spark"
        mock_table = MagicMock()
        mock_table.column_names = ["a"]
        mock_table.rows = [(i,) for i in range(3)]
        adapter._adapter.execute.return_value = (None, mock_table)
        with patch.object(adapter, "_resolve_dbt_sql", return_value="SELECT a FROM t"):
            query = SqlQuery(
                sql="SELECT * FROM {{ ref('t') }}", source="my_named_source"
            )
            result = adapter._execute(query)

        sent_kwargs = adapter._adapter.execute.call_args[1]
        assert sent_kwargs["limit"] is None
        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason == "max_rows"


class TestDbtAdapterCoexistsWithReader:
    """Regression: DbtAdapter must open DuckDB read-only with caller-supplied
    config_options so it coexists with a concurrent read-only connection that
    has enable_external_access=True (the playground SqlAdapter posture).
    """

    def test_coexists_with_external_access_reader(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Pre-fix, this raises "Can't open a connection to same database file
        with different configuration than existing connection" because DbtAdapter
        called build_adapter() without read_only=True, opening writable with
        enable_external_access=False against a file the holder opened read-only
        with enable_external_access=True.
        """
        import duckdb

        db_path = tmp_path / "warehouse.duckdb"
        setup = duckdb.connect(str(db_path))
        setup.execute("CREATE TABLE t (id INTEGER)")
        setup.execute("INSERT INTO t VALUES (42)")
        setup.close()

        (tmp_path / "dbt_project.yml").write_text(
            "name: testproj\nprofile: myprofile\n"
        )
        (tmp_path / "profiles.yml").write_text(
            "myprofile:\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            f"      path: {db_path}\n"
            "      schema: main\n"
            "      config_options:\n"
            "        enable_external_access: true\n"
            "  target: dev\n"
        )

        holder = duckdb.connect(
            str(db_path),
            read_only=True,
            config={"enable_external_access": True},
        )
        try:
            adapter = _dbt(dbt_project_path=tmp_path, local_project=local_project)
            result = adapter.execute(
                SqlQuery(sql="SELECT id FROM t", source="my_named_source")
            )
            assert result.error is None, result.error
            assert len(result.data) == 1
            assert result.data[0]["id"] == 42
        finally:
            holder.close()


class TestLoadManifestThroughProjectSeam:
    """The manifest must resolve via project.exists()/read_text(), not a raw
    Path built from dbt_project_path.

    dbt_project_path points at a real, empty temp directory (no manifest files
    on disk); the manifest content is served only through an InMemoryProject.
    A raw-Path implementation finds nothing there and the ref stays unresolved.
    """

    def test_manifest_reads_via_project_seam(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        manifest = {
            "nodes": {
                "model.x": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "analytics",
                    "alias": "orders",
                }
            },
            "sources": {},
        }
        project = in_memory_project(
            tmp_path, {"target/manifest.json": json.dumps(manifest)}
        )
        adapter = DbtAdapter(
            project=project, dbt_project_path=tmp_path, target_name="dev"
        )

        resolved = adapter._resolve_dbt_sql("SELECT * FROM {{ ref('orders') }}")

        assert resolved == "SELECT * FROM analytics.orders"


class TestParameterizedFilterHelper:
    """DbtAdapter must render {{ filter() }} through the parameterized pass.

    The default Jinja context binds filter()/filter_date_range() to raising
    stubs, so a dbt-project query using a variable filter died at render with
    ERR-INTERNAL instead of executing. The values reach the warehouse as
    adapter-inlined SQL literals — dbt's adapter.execute() takes no bindings.
    """

    def _executed_sql(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        sql: str,
        variables: dict[str, Any],
        dialect: str = "duckdb",
        lenient: bool = False,
    ) -> str:
        """Run `sql` through _execute and return the SQL adapter.execute() saw."""
        manifest = {
            "nodes": {
                "model.x": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "analytics",
                    "alias": "orders",
                }
            },
            "sources": {},
        }
        adapter = DbtAdapter(
            project=in_memory_project(
                tmp_path, {"target/manifest.json": json.dumps(manifest)}
            ),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._dialect = dialect
        mock_table = MagicMock()
        mock_table.column_names = ["region"]
        mock_table.rows = [("North",)]
        adapter._adapter.execute.return_value = (None, mock_table)

        result = adapter._execute(
            SqlQuery(sql=sql, source="my_named_source", lenient_variables=lenient),
            variables=variables,
        )

        assert result.error is None, result.error
        # The row-limit ceiling bounds the driver's fetch via a limit= kwarg
        # (see TestDbtAdapterMaxRowsCeiling) — it never touches SQL text, so
        # this is exactly the SQL adapter.execute() was sent.
        return str(adapter._adapter.execute.call_args.args[0])

    def test_filter_helper_executes_and_inlines_value(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A variable filter renders to a real predicate carrying the value."""
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', region) }}",
            {"region": "North"},
        )

        assert "region = 'North'" in executed
        assert "filter(" not in executed

    def test_unset_filter_value_renders_true(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """An unset filter value is the 'no filter' case, not an error.

        1=1 is the engine's "no constraint" sentinel (parameterized.py).
        """
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', region) }}",
            {"region": None},
        )

        assert "WHERE 1=1" in executed

    def test_filter_date_range_helper_executes(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """filter_date_range() is bound by the same pass as filter()."""
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter_date_range('order_date', date_range) }}",
            {"date_range": ["2024-01-01", "2024-03-31"]},
        )

        assert "'2024-01-01'" in executed
        assert "'2024-03-31'" in executed
        assert "filter_date_range(" not in executed

    def test_injection_payload_is_escaped_as_a_single_literal(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """An injection-shaped filter value arrives quoted, byte for byte.

        The payload's own quote is doubled, so the whole thing stays one string
        literal and the DROP never becomes a statement.
        """
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', region) }}",
            {"region": "'; DROP TABLE users; --"},
        )

        assert executed == (
            "SELECT region FROM analytics.orders "
            "WHERE region = '''; DROP TABLE users; --'"
        )

    def test_a_backslash_payload_cannot_close_its_literal(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """On an engine where `\\` escapes, doubling only `'` is not enough.

        `\\'` would leave the literal as `'\\''`, which closes at the escaped
        quote and hands the UNION to the parser as a top-level set operation.
        Runs at bigquery: the harness default (duckdb) is one of the engines
        where backslash escaping is a no-op, so it cannot see this. The value
        is asserted back off the parse tree, not just the emitted bytes.
        """
        import sqlglot

        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', region) }}",
            {"region": "\\' UNION ALL SELECT api_key, 2 FROM secrets --"},
            dialect="bigquery",
        )

        assert executed == (
            "SELECT region FROM analytics.orders "
            r"WHERE region = '\\\' UNION ALL SELECT api_key, 2 FROM secrets --'"
        )
        parsed = sqlglot.parse(executed, read="bigquery")
        assert len(parsed) == 1
        assert not list(parsed[0].find_all(sqlglot.expressions.Union))
        literal = parsed[0].find(sqlglot.expressions.Literal)
        assert literal is not None
        assert literal.this == "\\' UNION ALL SELECT api_key, 2 FROM secrets --"

    def test_a_backslash_in_a_value_is_doubled_on_a_backslash_engine(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A Windows path reaches MySQL as the eight characters it started as."""
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', region) }}",
            {"region": "C:\\Users"},
            dialect="mysql",
        )

        assert executed.endswith("WHERE region = 'C:\\\\Users'")

    def test_an_unregistered_warehouse_type_raises_rather_than_guessing(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """Binding a value needs the engine's literal grammar, unknown here.

        `get_dialect` answers postgres for anything unregistered, and postgres
        leaves backslashes alone — the wrong answer on, say, SingleStore (MySQL
        grammar), and wrong in the direction that puts part of a value in code
        position.
        """
        adapter = DbtAdapter(
            project=in_memory_project(
                tmp_path, {"target/manifest.json": json.dumps({"nodes": {}})}
            ),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._dialect = "singlestore"

        result = adapter._execute(
            SqlQuery(sql="SELECT {{ filter('region', region) }}", source="s"),
            variables={"region": "North"},
        )

        assert result.error is not None
        assert "singlestore" in result.error

    @pytest.mark.parametrize(
        "literal", ["'$1,000+'", "'Really?'", "'https://x.test/a?b=1'"]
    )
    def test_placeholder_shaped_literals_survive_untouched(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        literal: str,
    ) -> None:
        """Author SQL outside the filter span is untouched by binding.

        An earlier shape ran the whole statement through the parameterized pass,
        so a `$N` or `?` occurring in the author's own text was picked up as a
        placeholder and either raised or had a value spliced into it. Binding is
        now span-scoped, which is what makes the rest of the statement inert.
        """
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            f"SELECT {literal} AS tier FROM {{{{ ref('orders') }}}} "
            "WHERE {{ filter('region', region) }}",
            {"region": "North"},
        )

        assert f"SELECT {literal} AS tier" in executed
        assert "region = 'North'" in executed

    def test_placeholder_shaped_literal_with_no_variables_at_all(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The no-variable case is the common one — it must not raise.

        Most dbt queries have no variables, so rendering collects no params and
        there is nothing to substitute; a $N in the text is just text.
        """
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT '$1,000+' AS tier, 'Really?' AS q FROM {{ ref('orders') }}",
            {},
        )

        assert executed == (
            "SELECT '$1,000+' AS tier, 'Really?' AS q FROM analytics.orders"
        )

    def _resolver(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> DbtAdapter:
        """A DbtAdapter with working ref resolution, for _resolve_dbt_sql tests."""
        manifest = {
            "nodes": {
                "model.x": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "analytics",
                    "alias": "orders",
                }
            },
            "sources": {},
        }
        adapter = DbtAdapter(
            project=in_memory_project(
                tmp_path, {"target/manifest.json": json.dumps(manifest)}
            ),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._dialect = "duckdb"
        return adapter

    def test_variable_values_are_never_rendered_as_templates(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """Jinja in a variable *value* is data, not template source.

        Variable values arrive from URL query parameters, so resolution has to be
        a single pass: anything that re-renders the output of variable
        substitution is evaluating attacker-supplied text. The value carries its
        own filter() call here precisely because that is what a second pass would
        key on to decide to run.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT * FROM {{ ref('orders') }} WHERE region = '{{ region }}'",
            {"region": "{{ 7*7 }}{{ filter('region', 1) }}"},
        )

        assert "{{ 7*7 }}" in resolved
        assert "49" not in resolved

    def test_variable_values_cannot_reach_the_jinja_object_graph(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The sandbox-escape shape must survive as inert text.

        jinja2's default Environment is not sandboxed, so evaluating a value
        reaches __globals__ and from there os. Nothing in this repo uses
        SandboxedEnvironment — the invariant is that values are never rendered.
        """
        adapter = self._resolver(tmp_path, in_memory_project)
        payload = (
            "{{ cycler.__init__.__globals__.os.popen('id -un').read() }}"
            "{{ filter('region', 1) }}"
        )

        resolved = adapter._resolve_dbt_sql(
            "SELECT * FROM {{ ref('orders') }} WHERE region = '{{ region }}'",
            {"region": payload},
        )

        # Verbatim: not one character of the value was interpreted. Asserting on
        # the absence of command output instead would pass for the wrong reason
        # whenever the output happened not to be a substring of the payload.
        assert resolved == (
            f"SELECT * FROM analytics.orders WHERE region = '{payload}'"
        )

    @pytest.mark.parametrize(
        ("authored", "expected"),
        [
            ("r ILIKE '%{{ v }}%'", "r ILIKE '%North%'"),
            ("d >= '{{ v }}-01-01'", "d >= 'North-01-01'"),
            ("r = '{{ v }}'", "r = 'North'"),
            ("r = {{ v }}", "r = North"),
        ],
    )
    def test_variables_inside_literals_render_unchanged(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        authored: str,
        expected: str,
    ) -> None:
        """Only the filter helpers are bound — variables render as they always did.

        A variable written inside a larger literal (`'%{{ v }}%'`, a date built
        from a year) is the case that breaks if variables are bound instead of
        substituted: a bound value arrives already quoted, so it lands as
        `'%'North'%'` and the statement no longer parses.
        """
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            f"SELECT region FROM {{{{ ref('orders') }}}} WHERE {authored}",
            {"v": "North"},
        )

        assert executed == f"SELECT region FROM analytics.orders WHERE {expected}"

    def test_variables_inside_literals_still_render_alongside_a_filter(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The binding pass must not disturb variables it is not binding."""
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE d >= '{{ yr }}-01-01' AND {{ filter('region', region) }}",
            {"yr": "2024", "region": "North"},
        )

        assert "d >= '2024-01-01'" in executed
        assert "region = 'North'" in executed

    def test_lenient_variables_tolerates_an_undefined_variable(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """strict must reach the binding half, not just the variable half.

        The undefined variable is *inside* the filter span, which is the only
        placement that discriminates: binding a span whose own variable is
        defined gives the same result either way, so a test that puts it outside
        passes even if strict never reaches the binder. Unset means "no
        constraint" (1=1) for a lenient board, and an error for a strict one.
        """
        adapter = self._resolver(tmp_path, in_memory_project)
        sql = "SELECT region FROM {{ ref('orders') }} WHERE {{ filter('region', missing) }}"

        lenient = adapter._resolve_dbt_sql(sql, {}, strict=False)

        assert lenient == "SELECT region FROM analytics.orders WHERE 1=1"
        with pytest.raises(JinjaError):
            adapter._resolve_dbt_sql(sql, {}, strict=True)

    def test_lenient_variables_flag_reaches_the_renderer_through_execute(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The SqlQuery flag, not just the keyword, has to do the work."""
        executed = self._executed_sql(
            tmp_path,
            in_memory_project,
            "SELECT region FROM {{ ref('orders') }} "
            "WHERE {{ filter('region', missing) }}",
            {},
            lenient=True,
        )

        assert executed == "SELECT region FROM analytics.orders WHERE 1=1"

    def test_strict_undefined_variable_via_execute_names_template_rendering(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """An undefined strict variable must be attributed to template
        rendering, not adapter setup — the mislabel that obscured the
        original filter() bug report ("dbt adapter setup failed:
        Undefined variable...").
        """
        manifest = {
            "nodes": {
                "model.x": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "analytics",
                    "alias": "orders",
                }
            },
            "sources": {},
        }
        adapter = DbtAdapter(
            project=in_memory_project(
                tmp_path, {"target/manifest.json": json.dumps(manifest)}
            ),
            dbt_project_path=tmp_path,
            target_name="dev",
        )
        adapter._adapter = MagicMock()
        adapter._dialect = "duckdb"

        result = adapter._execute(
            SqlQuery(
                sql="SELECT region FROM {{ ref('orders') }} WHERE {{ filter('region', missing) }}",
                source="my_named_source",
            ),
            variables={},
        )

        assert result.error is not None
        assert result.error.startswith("query template rendering failed:")
        assert "dbt adapter setup" not in result.error
        adapter._adapter.execute.assert_not_called()

    def test_a_loop_variable_can_be_filtered_on(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A filter() argument may come from the loop that emits the span.

        Jinja invokes the helper, so the loop variable is in scope for it
        exactly as for any other expression in the body. The masking design
        could not do this: it bound each span by re-rendering that span's text
        against the board variables alone, where `u` does not exist.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT region FROM {{ ref('orders') }} WHERE "
            "{% for u in units %}{{ filter('region', u) }} OR {% endfor %} 1=0",
            {"units": ["North", "South"]},
        )

        assert resolved == (
            "SELECT region FROM analytics.orders WHERE "
            "region = 'North' OR region = 'South' OR  1=0"
        )

    def test_a_set_variable_can_be_filtered_on(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """`{% set %}` is in scope for filter() too, for the same reason."""
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT region FROM {{ ref('orders') }} "
            "{% set r = region | upper %}WHERE {{ filter('region', r) }}",
            {"region": "north"},
        )

        assert resolved == "SELECT region FROM analytics.orders WHERE region = 'NORTH'"

    @pytest.mark.parametrize("pipe", ["| upper"])
    def test_a_predicate_transform_that_mangles_the_placeholder_is_refused(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        pipe: str,
    ) -> None:
        """A pipe that corrupts the placeholder is refused, not run unfiltered.

        The helpers emit a placeholder, so a pipe lands on that rather than on
        a value. Upper-casing it means nothing downstream matches, the
        parameter is never substituted, and the query would run with no
        constraint at all — so it is refused rather than allowed to return
        wrong rows with no error. The match is on the shared property, not on
        which of the two checks fires: the placeholder-present check sees the
        exact token missing, the inline guard sees the mangled one left behind.

        `| upper` is the only stock Jinja filter that reaches the token: it is
        already lower case, and NUL delimits it, which `| title` and
        `| capitalize` treat as a word boundary and leave alone.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        with pytest.raises(JinjaError, match="would never be bound"):
            adapter._resolve_dbt_sql(
                "SELECT region FROM {{ ref('orders') }} "
                f"WHERE {{{{ filter('region', region) {pipe} }}}}",
                {"region": "North"},
            )

    @pytest.mark.parametrize("pipe", ["| lower", "| trim", '| replace("\'", "")'])
    def test_a_predicate_transform_cannot_strip_a_value_of_its_escaping(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        pipe: str,
    ) -> None:
        """A pipe that leaves the placeholder alone binds the value as usual.

        This is the property that makes the placeholder round trip worth
        keeping. `| replace("'", "")` is a no-op on a quote-free token, so an
        author cannot use it to strip the quoting off a value: the escaping is
        applied *after* the pipe has run, by the inline pass. Emit the escaped
        literal during the render instead and the same pipe takes the quotes
        straight back off, putting a URL-supplied value in code position.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT region FROM {{ ref('orders') }} "
            f"WHERE {{{{ filter('region', region) {pipe} }}}}",
            {"region": "' OR 1=1 --"},
        )

        assert resolved == (
            "SELECT region FROM analytics.orders WHERE region = ''' OR 1=1 --'"
        )

    def test_a_bound_value_whose_placeholder_never_lands_is_refused(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A collected value with nowhere to bind is refused, not dropped.

        Binding a predicate under a name and then not emitting it leaves the
        value collected and its placeholder absent. The inline guard cannot see
        this — it only matches a token that survived mangled — so the query
        would go to the warehouse with no constraint at all and return every
        row. `{% set %}` binding is a pattern this change newly supports, which
        is what makes the mistake reachable.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        with pytest.raises(JinjaError, match="placeholder is missing"):
            adapter._resolve_dbt_sql(
                "{% set p = filter('region', region) %}"
                "SELECT region FROM {{ ref('orders') }}"
                "{% if show %} WHERE {{ p }}{% endif %}",
                {"region": "North", "show": False},
            )

    def test_a_filter_that_collides_two_placeholders_is_refused(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """Rewriting one token into another's spelling drops the second value.

        Both predicates then read parameter 1 and parameter 2 binds nowhere —
        the mangled-token guard sees a well-formed token and passes it.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        with pytest.raises(JinjaError, match="placeholder is missing"):
            adapter._resolve_dbt_sql(
                "SELECT region FROM {{ ref('orders') }} WHERE "
                "{{ filter('region', region) }} AND "
                '{{ filter(\'tier\', tier) | replace("2", "1") }}',
                {"region": "North", "tier": "gold"},
            )

    def test_a_predicate_bound_through_set_still_renders(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """Naming a predicate with {% set %} and emitting it later is fine."""
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "{% set p = filter('region', region) %}"
            "SELECT region FROM {{ ref('orders') }} WHERE {{ p }}",
            {"region": "North"},
        )

        assert resolved == "SELECT region FROM analytics.orders WHERE region = 'North'"

    @pytest.mark.parametrize(
        "call",
        [
            "filter('region', region, 'DROP')",
            "filter('region; DROP TABLE t', region)",
            "filter('region', region, none='maybe')",
            "filter_date_range('d', region)",
        ],
    )
    def test_a_bad_filter_argument_is_a_coded_jinja_error(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        call: str,
    ) -> None:
        """The helpers' own argument validation reaches the author coded.

        They raise ValueError from inside the render; unwrapped that reaches
        the executor with no error code and stamps ERR-INTERNAL, which this
        repo treats as a bug rather than an author-facing error. The
        parameterized path used to supply the coding, and moving the helpers
        into the plain-Jinja context is what took them out from under it.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        with pytest.raises(JinjaError):
            adapter._resolve_dbt_sql(
                f"SELECT region FROM {{{{ ref('orders') }}}} WHERE {{{{ {call} }}}}",
                {"region": "North"},
            )

    @pytest.mark.parametrize(
        ("units", "expected"),
        [
            ([1], "region = 'North' OR  1=0"),
            ([1, 2], "region = 'North' OR region = 'North' OR  1=0"),
        ],
    )
    def test_a_loop_may_emit_the_same_filter_span_many_times(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        units: list[int],
        expected: str,
    ) -> None:
        """A {% for %} calls the helper once per iteration, emitting N predicates.

        Both counts are pinned because an earlier version rejected the second:
        one iteration rendered and two raised, so a multiselect became an error
        on the second click.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT region FROM {{ ref('orders') }} WHERE "
            "{% for u in units %}{{ filter('region', region) }} OR {% endfor %} 1=0",
            {"units": units, "region": "North"},
        )

        assert resolved == f"SELECT region FROM analytics.orders WHERE {expected}"

    def test_a_dropped_span_is_not_bound_at_all(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """A span a false branch removed must not be evaluated at all.

        Evaluating it anyway raises on its undefined variable — for a predicate
        the query was never going to contain. Jinja skips the branch, so the
        helper is simply never called.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            "SELECT region FROM {{ ref('orders') }}"
            "{% if show %} WHERE {{ filter('region', missing) }}{% endif %}",
            {"show": False},
        )

        assert resolved == "SELECT region FROM analytics.orders"

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("{{ filter('region', region) }}", "WHERE region = 'North'"),
            ("{{- filter('region', region) }}", "WHEREregion = 'North'"),
            ("{{ filter('region', region) -}}", "WHERE region = 'North'"),
            ("{{- filter('region', region) -}}", "WHEREregion = 'North'"),
        ],
    )
    def test_whitespace_control_spellings_are_bound(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
        spelling: str,
        expected: str,
    ) -> None:
        """`{{-` / `-}}` bind, and mean what Jinja says they mean.

        `{{-` eats the space before the tag, so the two `{{-` spellings really
        do run WHERE into the predicate — the author's bug, reported the same
        way the SqlAdapter path already reports it. The masking design swallowed
        the hyphen along with the span, which silently made the same board
        render differently depending on which adapter ran it.
        """
        adapter = self._resolver(tmp_path, in_memory_project)

        resolved = adapter._resolve_dbt_sql(
            f"SELECT region FROM {{{{ ref('orders') }}}} WHERE {spelling}",
            {"region": "North"},
        )

        assert resolved.rstrip() == f"SELECT region FROM analytics.orders {expected}"
