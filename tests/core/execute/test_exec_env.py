"""Unit tests for ExecEnv instance methods."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig, load_project_sources
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.backends.base import BackendRegistry
from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
from dbt_charts.core.execute.env import (
    ExecEnv,
    close_env,
    make_exec_env,
    warehouse_routes,
)
from dbt_charts.core.execute.run import run_query


class TestSourceAwareRouting:
    """Source-aware adapter routing in ExecEnv.execute."""

    def test_unknown_source_with_dbt_project_present_still_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Regression (dbt-labs/dbt-charts#45): a `dbt_project.yml` in the
        project root links the dbt project and puts a DbtContext in scope. Plain
        SQL naming a typo'd/unconfigured source must still raise
        ERR-SOURCE-NOT-FOUND — it must never silently execute against
        DuckDBBackend's `:memory:` default just because a dbt project happens
        to be present.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
        from dbt_charts.core.execute.env import build_exec_env

        (tmp_path / "dbt_project.yml").write_text(
            "name: repro_project\nversion: '1.0.0'\nprofile: repro_project\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  analytics_warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        project = local_project(tmp_path)
        registry = build_exec_env(project)

        for offending in ("analytics_warehous", "default", "duckdb"):
            result = run_query(registry, SqlQuery(sql="SELECT 1", source=offending))
            assert (
                result.error is not None and result.error.code is ERR_SOURCE_NOT_FOUND
            ), f"source={offending!r} silently executed instead of raising"
            assert result.data == []

    @pytest.mark.parametrize("source_type", ["duckdb", "sqlite"])
    def test_file_source_seam_closed_when_adapter_absent(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        source_type: str,
    ) -> None:
        """A registry that omits the file-engine backends (as Cloud's
        build_cloud_exec_env does) fails closed: a duckdb/sqlite source
        resolves but its kind has no route, so execute returns a fail-closed
        error naming the kind rather than falling back to a warehouse backend."""
        from unittest.mock import patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.source import (
            SQLiteSourceConfig,
        )

        # Only warehouse routes — no DuckDB/SQLite adapter (the Cloud shape).
        registry = make_exec_env(
            project=local_project(tmp_path),
            backends=BackendRegistry(warehouse_routes(None)),
        )

        resolved = (
            DuckDBSourceConfig(type="duckdb", path=":memory:")
            if source_type == "duckdb"
            else SQLiteSourceConfig(type="sqlite", path="db.sqlite")
        )
        query = SqlQuery(sql="SELECT 1", source="local_file")
        with patch.object(registry.resolver, "resolve", return_value=resolved):
            result = run_query(registry, query)

        assert not result.is_success
        error = str(result.error or "").lower()
        assert f"no backend for source kind '{source_type}'" in error

    def test_duckdb_routes_to_its_backend(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """DuckDB is owned exclusively by its backend."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.backends.base import BackendRegistry

        registry = make_exec_env(
            project=local_project(tmp_path),
            project_sources=ProjectSourcesConfig(
                sources={"local_file": {"type": "duckdb", "path": ":memory:"}}
            ),
            backends=BackendRegistry({"duckdb": DuckDBBackend()}),
        )

        result = run_query(registry, SqlQuery(sql="SELECT 1 AS n", source="local_file"))
        assert result.error is None
        assert result.data == [{"n": 1}]
        close_env(registry)

    @pytest.mark.parametrize("source_type", ["duckdb", "sqlite"])
    def test_warehouse_routes_never_serve_the_file_engines(
        self, source_type: str
    ) -> None:
        """DuckDB and SQLite run in-process on their own adapters, never on a
        warehouse backend."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig

        source = (
            DuckDBSourceConfig(type="duckdb", path=":memory:")
            if source_type == "duckdb"
            else SQLiteSourceConfig(type="sqlite", path="db.sqlite")
        )
        assert not BackendRegistry(warehouse_routes(None)).serves(source)


class TestClose:
    def test_close_closes_the_backends(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        backend = MagicMock()
        registry = make_exec_env(
            project=local_project(tmp_path),
            backends=BackendRegistry({"postgres": backend}),
        )

        close_env(registry)

        backend.close.assert_called_once_with()

    def test_interrupt_reaches_the_backends(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        backend = MagicMock()
        registry = make_exec_env(
            project=local_project(tmp_path),
            backends=BackendRegistry({"postgres": backend}),
        )

        registry.backends.interrupt()

        backend.interrupt.assert_called_once_with()

    def test_close_does_not_close_an_injected_file_materializer(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An injected materializer (Cloud's, shared with the session that
        built it) is not the registry's to close — only a materializer the
        registry itself built via file_materializer_factory is.
        """
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )

        project = local_project(tmp_path)
        materializer = FileSourceMaterializer(project)
        closed = False
        original_close = materializer.close

        def track_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        materializer.close = track_close  # type: ignore[method-assign]
        registry = make_exec_env(project=project, file_materializer=materializer)

        close_env(registry)

        assert not closed

    def test_close_closes_a_factory_built_file_materializer(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A materializer the registry built lazily via
        file_materializer_factory is the one materializer close() owns."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "orders.csv").write_text("region,amount\nNorth,1\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
        )
        project = local_project(tmp_path)
        built: list[FileSourceMaterializer] = []

        def factory() -> FileSourceMaterializer:
            materializer = FileSourceMaterializer(project)
            built.append(materializer)
            return materializer

        registry = make_exec_env(
            project=project,
            project_sources=load_project_sources(project),
            file_materializer_factory=factory,
        )
        run_query(registry, SqlQuery(sql="SELECT * FROM orders", source="marts"))
        assert len(built) == 1
        closed = False
        original_close = built[0].close

        def track_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        built[0].close = track_close  # type: ignore[method-assign]

        close_env(registry)

        assert closed


class TestSourcelessRejectScopedToSql:
    """The closed-allowlist source-less reject fires for SQL queries only.

    A hosted/multi-tenant surface (AllowlistedSourceResolver) rejects a
    source-less SQL query because it would otherwise fall through to the engine's
    fallback DuckDB — an SSRF / local-file-read vector. But inline query types
    (values / http) have no source connection at all, so gating them
    on a registered source is wrong and breaks every KPI / literal-data chart in
    deployed mode. Regression: PR #5113 rejected authored=None for every query
    type at the resolver, so values queries failed to render on the deployed
    playground.
    """

    def _hosted_registry(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> ExecEnv:
        from dbt_charts.core.execute.env import build_exec_env
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        return build_exec_env(
            local_project(tmp_path),
            read_only=False,
            resolver=AllowlistedSourceResolver(),
        )

    def test_sourceless_values_query_renders_on_hosted_surface(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import ValuesQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = run_query(registry, ValuesQuery(rows=[{"metric": "revenue", "n": 42}]))
        assert result.error is None, result.error
        assert result.data == [{"metric": "revenue", "n": 42}]

    def test_sourceless_sql_query_still_rejected_on_hosted_surface(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = run_query(registry, SqlQuery(sql="SELECT 1 AS one", source=None))
        assert result.error is not None
        assert "names no source" in str(result.error).lower()

    def test_named_non_sql_query_still_resolves_on_hosted_surface(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The `authored is None` half of the skip predicate is load-bearing.

        A non-SQL query that *names* a source (SchemaQuery(source="x")) is not
        source-less, so it must still route through the resolver — an unknown name
        is rejected on the closed allowlist. A bare `not is_sql_query(query)` skip
        would wrongly bypass this.
        """
        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = run_query(registry, SchemaQuery(source="unregistered"))
        assert result.error is not None
        assert "unregistered" in str(result.error)

    def test_execute_threads_query_name_into_source_not_found_message(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """ERR-SOURCE-NOT-FOUND's message_template requires `query_name`.
        `execute()`'s `query_name` kwarg must reach the resolver and appear
        in the rendered error, not just avoid a KeyError.
        """
        from dbt_charts.core.compile.models.query.normalized import SchemaQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = run_query(
            registry, SchemaQuery(source="unregistered"), query_name="my_named_query"
        )
        assert result.error is not None
        assert "my_named_query" in str(result.error)


class TestSourcelessSqlRejectedOnDefaultResolver:
    """execute() itself closes the default-DuckDB fallback — not just
    resolve_source_config(None). Before this fix, a sourceless SqlQuery on
    the plain (non-hosted) DefaultSourceResolver silently ran against the
    build-time :memory: DuckDBBackend instead of raising."""

    def _registry_with_a_configured_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> ExecEnv:
        from dbt_charts.core.execute.env import build_exec_env

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        return build_exec_env(local_project(tmp_path), read_only=False)

    def test_sourceless_sql_query_raises_source_not_named(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        registry = self._registry_with_a_configured_source(tmp_path, local_project)
        result = run_query(registry, SqlQuery(sql="SELECT 1 AS one", source=None))
        assert result.error is not None
        assert "names no source" in str(result.error)

    def test_sourceless_values_query_still_succeeds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import ValuesQuery

        registry = self._registry_with_a_configured_source(tmp_path, local_project)
        result = run_query(registry, ValuesQuery(rows=[{"metric": "revenue", "n": 42}]))
        assert result.error is None, result.error
        assert result.data == [{"metric": "revenue", "n": 42}]


def test_file_source_without_a_materializer_still_refuses(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A file source resolves fine — but a registry with no file_materializer
    (the default) still cannot run one. The seam is opt-in: a caller that wants
    ad-hoc file-source queries must pass file_materializer/file_materializer_factory
    to ExecEnv (or ProjectSession), never get one invented for free.
    """
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    registry = make_exec_env(
        project=local_project(tmp_path),
        project_sources=load_project_sources(local_project(tmp_path)),
    )

    result = run_query(registry, SqlQuery(sql="SELECT 1", source="marts"))

    assert result.error is not None
    assert "marts" in str(result.error)
    assert "materializer" in str(result.error)


def test_schema_query_on_a_file_source_refuses_even_with_a_materializer(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """SchemaQuery is the other query that can carry a source, and stays
    refused unconditionally — schema introspection on a file source (the
    /data explorer, or an authored `type: schema` query) is out of scope
    here and deferred to a follow-up task, regardless of whether a
    materializer is wired for SqlQuery execution.
    """
    from dbt_charts.core.compile.models.query.normalized import SchemaQuery
    from dbt_charts.core.execute.file_source_materializer import (
        FileSourceMaterializer,
    )

    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    project = local_project(tmp_path)
    registry = make_exec_env(
        project=project,
        project_sources=load_project_sources(project),
        file_materializer=FileSourceMaterializer(project),
    )

    result = run_query(registry, SchemaQuery(source="marts"))

    assert result.error is not None
    assert "marts" in str(result.error)
    # Distinguish this refusal from the "no materializer configured" one
    # (test_file_source_without_a_materializer_still_refuses) — this fires
    # unconditionally regardless of the materializer wired above.
    assert "Schema introspection" in str(result.error)


class TestFileSourceDispatch:
    """execute() runs a file-source SqlQuery through an injected materializer.

    Regression coverage for `dct query <file-source> 'SELECT ...'` /
    `dct query <board> <query>` (agent_api.execute_query / query_board share
    this path via ExecEnv.execute).
    """

    @staticmethod
    def _csv_project(
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        rows: list[tuple[str, int]],
    ) -> FilesystemProject:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        lines = "\n".join(f"{region},{amount}" for region, amount in rows)
        (data_dir / "orders.csv").write_text(f"region,amount\n{lines}\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
        )
        return local_project(tmp_path)

    def _registry_with_materializer(self, project: FilesystemProject) -> ExecEnv:
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )

        return make_exec_env(
            project=project,
            project_sources=load_project_sources(project),
            file_materializer=FileSourceMaterializer(project),
        )

    def test_sql_query_returns_rows_from_a_real_csv(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(
            tmp_path, local_project, [("North", 100), ("South", 200)]
        )
        registry = self._registry_with_materializer(project)

        result = run_query(
            registry,
            SqlQuery(sql="SELECT * FROM orders ORDER BY amount", source="marts"),
        )

        assert result.error is None, result.error
        assert [r["region"] for r in result.data] == ["North", "South"]

    def test_registry_applies_the_select_only_and_setup_guards(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(tmp_path, local_project, [("North", 100)])
        registry = self._registry_with_materializer(project)

        mutating = run_query(
            registry, SqlQuery(sql="DROP TABLE orders", source="marts")
        )
        bad_setup = run_query(
            registry,
            SqlQuery(sql="SELECT 1", setup_sql="DROP TABLE orders", source="marts"),
        )

        assert mutating.error is not None
        assert mutating.error.code.code == "ERR-MUTATING-SQL"
        assert bad_setup.error is not None
        assert bad_setup.error.code.code == "ERR-MUTATING-SQL"

    def test_missing_file_on_disk_returns_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A `files:` entry naming a file absent from disk makes
        Project.file_version (Path.stat()) raise FileNotFoundError — an
        OSError, not a DbtChartsError/RuntimeError. Must surface as a
        QueryResult error, not escape execute() as an unhandled exception.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/missing.csv\n"
        )
        project = local_project(tmp_path)
        registry = self._registry_with_materializer(project)

        result = run_query(
            registry, SqlQuery(sql="SELECT * FROM orders", source="marts")
        )

        assert result.error is not None
        # Pin which arm answered: without this the test would also pass if a
        # future change made `marts` fail at source resolution instead.
        assert "File-source execution" in str(result.error)

    def test_header_only_csv_returns_an_error_not_a_crash(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A CSV parsing to zero data rows raises a bare ValueError inside the
        materializer — neither a DbtChartsError/RuntimeError nor an OSError.
        It is an ordinary authoring mistake, so it must surface as a
        QueryResult error rather than escaping execute() unhandled.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(tmp_path, local_project, [])
        registry = self._registry_with_materializer(project)

        result = run_query(
            registry, SqlQuery(sql="SELECT * FROM orders", source="marts")
        )

        assert result.error is not None
        assert "File-source execution" in str(result.error)
        # Pin the raise site too: were this ValueError later promoted to a typed
        # DbtChartsError, the prefix alone would keep passing while the
        # ValueError arm of the catch lost its only coverage.
        assert "contain no rows" in str(result.error)

    def test_execution_error_names_the_operation_once(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The cache backend adds SQL context; the registry names the operation
        exactly once, so a DuckDB rejection reaches the author without the
        phrase "File-source execution failed" stuttering before the actual
        Binder Error.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(tmp_path, local_project, [("North", 100)])
        registry = self._registry_with_materializer(project)

        result = run_query(
            registry, SqlQuery(sql="SELECT nonexistent_col FROM orders", source="marts")
        )

        assert result.error is not None
        assert str(result.error).count("File-source execution failed") == 1
        # The backend's own context still reaches the author.
        assert "nonexistent_col" in str(result.error)

    def test_lenient_variables_is_honored_on_the_file_source_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """query.lenient_variables must reach materialize_and_run's own
        render — the same ``strict=not query.lenient_variables`` every other
        adapter honors — for a query with no board.queries, where params
        stays None and the materializer does its own render.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(tmp_path, local_project, [("North", 100)])
        registry = self._registry_with_materializer(project)
        sql = "SELECT * FROM orders WHERE region = '{{ region }}'"

        strict_result = run_query(registry, SqlQuery(sql=sql, source="marts"))
        assert strict_result.error is not None

        lenient_result = run_query(
            registry, SqlQuery(sql=sql, source="marts", lenient_variables=True)
        )
        assert lenient_result.error is None, lenient_result.error

    def test_query_ref_is_expanded_before_dispatch(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Regression test for the ordering hazard: the guard used to sit
        above compose, so a file-source query carrying
        `{{ queries.X }}` reached the materializer unexpanded. Dispatch must
        sit below composition so the ref is resolved first.
        """
        from dbt_charts.core.compile import compile as compile_board

        project = self._csv_project(
            tmp_path, local_project, [("North", 100), ("South", 200)]
        )
        registry = self._registry_with_materializer(project)

        board_yaml = (
            "title: probe\n"
            "text: probe board\n"
            "queries:\n"
            "  base:\n"
            "    sql: SELECT * FROM orders\n"
            "    source: marts\n"
            "  wrapper:\n"
            "    sql: \"SELECT * FROM {{ queries.base }} WHERE region = 'North'\"\n"
            "    source: marts\n"
        )
        compile_result = compile_board(board_yaml)
        assert compile_result.success, compile_result.errors
        board = compile_result.board
        assert board is not None

        result = run_query(
            registry, board.queries["wrapper"], board=board, query_name="wrapper"
        )

        assert result.error is None, result.error
        assert [r["region"] for r in result.data] == ["North"]

    def test_limit_and_max_rows_bound_the_file_source_fetch(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        rows = [("North", i) for i in range(10)]
        project = self._csv_project(tmp_path, local_project, rows)
        registry = self._registry_with_materializer(project)

        # Author's own limit: bounds the fetch to exactly that many rows.
        limited = run_query(
            registry, SqlQuery(sql="SELECT * FROM orders", source="marts", limit=2)
        )
        assert limited.error is None, limited.error
        assert len(limited.data) == 2
        assert limited.truncated_reason is None

        # No author limit, ceiling below the source's row count: truncates at
        # the ceiling and flags it — the same contract the dbt backend honors.
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        unbounded = run_query(
            registry, SqlQuery(sql="SELECT * FROM orders", source="marts")
        )
        assert unbounded.error is None, unbounded.error
        assert len(unbounded.data) == 3
        assert unbounded.truncated_reason == "max_rows"
