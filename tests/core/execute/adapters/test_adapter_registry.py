"""Unit tests for AdapterRegistry instance methods.

Pins `AdapterRegistry.list_sql_sources` (empty case, in-memory marker,
deterministic sort), `AdapterRegistry.register_source` (synthetic source
registration), and `AdapterRegistry.resolve_source_config`.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter


def _registry_with_sources(
    tmp_path: Path, sources_yaml: str, local_project: Callable[..., FilesystemProject]
) -> AdapterRegistry:
    """Build an AdapterRegistry whose SourceRegistry reads from tmp_path."""
    (tmp_path / "dbt_charts.yml").write_text(sources_yaml)
    return AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=load_project_sources(local_project(tmp_path)),
    )


class TestListSqlSources:
    def test_empty_registry_returns_empty_list(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        assert AdapterRegistry(project=local_project(Path())).list_sql_sources() == []

    def test_registry_without_sources_returns_empty_list(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = AdapterRegistry(project=local_project(tmp_path))
        assert registry.list_sql_sources() == []

    def test_in_memory_duckdb_source_gets_in_memory_marker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        result = registry.list_sql_sources()
        assert result == [
            {"name": "mem", "type": "duckdb", "path": ":memory:", "in_memory": True}
        ]

    def test_file_backed_duckdb_source_omits_in_memory_marker(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        db_path = tmp_path / "warehouse.duckdb"
        registry = _registry_with_sources(
            tmp_path,
            f"sources:\n  warehouse:\n    type: duckdb\n    path: '{db_path}'\n",
            local_project,
        )

        result = registry.list_sql_sources()
        assert result == [{"name": "warehouse", "type": "duckdb", "path": str(db_path)}]

    def test_results_are_sorted_by_name(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            (
                "sources:\n"
                "  zeta:\n    type: duckdb\n    path: ':memory:'\n"
                "  alpha:\n    type: duckdb\n    path: ':memory:'\n"
                "  mike:\n    type: duckdb\n    path: ':memory:'\n"
            ),
            local_project,
        )

        result: list[dict[str, Any]] = registry.list_sql_sources()
        assert [s["name"] for s in result] == ["alpha", "mike", "zeta"]


class TestRegisterSource:
    """Synthetic source registration lands on the registry, not the adapter."""

    def test_register_source_appears_in_list(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = AdapterRegistry(project=local_project(Path()))
        registry.register_source("synth", {"type": "duckdb", "path": ":memory:"})
        names = [s["name"] for s in registry.list_sql_sources()]
        assert names == ["synth"]

    def test_register_source_is_idempotent_first_wins(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = AdapterRegistry(project=local_project(Path()))
        registry.register_source("synth", {"type": "duckdb", "path": ":memory:"})
        registry.register_source("synth", {"type": "duckdb", "path": "/tmp/a.duckdb"})
        config = registry.resolve_source_config("synth")
        assert config["path"] == ":memory:"

    def test_project_source_wins_over_synthetic(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  shared:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )
        registry.register_source(
            "shared", {"type": "duckdb", "path": "/tmp/synth.duckdb"}
        )
        config = registry.resolve_source_config("shared")
        assert config["path"] == ":memory:"


class TestResolveSourceConfig:
    """resolve_source_config must raise when source is None — no lossy fallback."""

    def test_no_source_raises_even_with_duckdb_adapter_registered(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No default-DuckDB fallback: a real registry built via
        build_adapter_registry() (which registers a DuckDBAdapter) still raises
        for source=None — there is no stored default to fall back to."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_NO_DEFAULT_SOURCE
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(local_project(Path()))

        with pytest.raises(DbtChartsError) as exc_info:
            registry.resolve_source_config(source=None)
        assert exc_info.value.code is ERR_NO_DEFAULT_SOURCE

    def test_no_source_non_duckdb_raises(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Non-DuckDB SqlAdapter has credentials/host/port that source_config_from_url
        # would drop — must raise instead of silently producing a mangled config.
        from dbt_charts.core.diagnostics.codes_execute import ERR_NO_DEFAULT_SOURCE

        registry = AdapterRegistry(project=local_project(Path()))
        registry.register(
            SqlAdapter(
                project=local_project(Path("/tmp")),
                dbt_project_path=None,
                profile_type="postgres",
            )
        )

        with pytest.raises(DbtChartsError) as exc_info:
            registry.resolve_source_config(source=None)
        assert exc_info.value.code is ERR_NO_DEFAULT_SOURCE

    def test_no_source_no_adapter_still_raises(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # No registry at all — must also raise (not the "no SQL adapter" branch).
        from dbt_charts.core.diagnostics.codes_execute import ERR_NO_DEFAULT_SOURCE

        with pytest.raises(DbtChartsError) as exc_info:
            AdapterRegistry(project=local_project(Path())).resolve_source_config(
                source=None
            )
        assert exc_info.value.code is ERR_NO_DEFAULT_SOURCE
        # No sources configured → `available` is an empty Sequence[str], which the
        # display path renders "none configured" — not "" or a bare list repr.
        assert "none configured" in exc_info.value.message

    def test_no_default_source_error_renders_available_as_joined_names(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The sourceless-SQL error passes `available` as a Sequence[str] like
        every other ERR-* caller, so the diagnostic joins the configured names
        for display — never a raw Python list repr. Guards this raise site
        against regressing to a pre-joined string, which would diverge from the
        contract and crash the moment this code gains a hint_generator."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_NO_DEFAULT_SOURCE

        registry = _registry_with_sources(
            tmp_path,
            "sources:\n"
            "  alpha:\n    type: duckdb\n    path: ':memory:'\n"
            "  beta:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        with pytest.raises(DbtChartsError) as exc_info:
            registry.resolve_source_config(source=None)
        assert exc_info.value.code is ERR_NO_DEFAULT_SOURCE
        message = exc_info.value.message
        assert "alpha" in message and "beta" in message
        assert "['" not in message

    def test_named_source_still_resolves(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = _registry_with_sources(
            tmp_path,
            "sources:\n  mydb:\n    type: duckdb\n    path: ':memory:'\n",
            local_project,
        )

        config = registry.resolve_source_config(source="mydb")
        assert config["type"] == "duckdb"


class TestSourceAwareRouting:
    """Source-aware adapter routing in AdapterRegistry.execute."""

    def test_dbt_jinja_with_unresolved_source_routes_to_dbt_adapter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """SQL with {{ ref() }} and a named source that resolves to None must go
        to DbtAdapter (which owns manifest resolution), not DuckDBAdapter.
        DbtAdapter is registered before DuckDBAdapter, so it wins the source-less
        dbt-jinja case that both are eligible for.
        """
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
        from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja

        registry = AdapterRegistry(project=local_project(tmp_path))

        # Mock DbtAdapter (we check routing, not execution) whose can_execute
        # mirrors the real predicate: source-less SQL that carries dbt jinja.
        dbt_adapter = MagicMock(spec=DbtAdapter)
        dbt_adapter.supported_types = {"sql"}
        dbt_adapter.dbt_project_path = tmp_path
        dbt_adapter.can_execute = lambda q, sc: sc is None and has_dbt_jinja(q.sql)
        dbt_adapter.execute = MagicMock(
            return_value=MagicMock(data=[], error=None, is_success=True)
        )
        registry.register(dbt_adapter)

        duckdb_adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        registry.register(duckdb_adapter)

        sql_adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="duckdb",
        )
        registry.register(sql_adapter)

        # A query with a string source that won't resolve to any entry in dbt_charts.yml
        # and SQL that contains dbt-jinja {{ ref() }}.
        query = SqlQuery(
            sql="SELECT * FROM {{ ref('orders') }}",
            source="my_unknown_source",
        )

        # Patch the resolver to return None (simulates dbt-context: unknown named source)
        with patch.object(
            registry._resolver,
            "resolve",
            return_value=None,
        ):
            registry.execute(query)

        # DbtAdapter must have been called, not DuckDBAdapter
        dbt_adapter.execute.assert_called_once()

    def test_unknown_source_with_dbt_project_present_still_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Regression (dbt-labs/dbt-charts#45): a `dbt_project.yml` in the
        project root registers DbtAdapter and puts a DbtContext in scope. Plain
        SQL naming a typo'd/unconfigured source must still raise
        ERR-SOURCE-NOT-FOUND — it must never silently execute against
        DuckDBAdapter's `:memory:` default just because a dbt project happens
        to be present.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_project.yml").write_text(
            "name: repro_project\nversion: '1.0.0'\nprofile: repro_project\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  analytics_warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project)

        for offending in ("analytics_warehous", "default", "duckdb"):
            result = registry.execute(SqlQuery(sql="SELECT 1", source=offending))
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
        """A registry that omits the file-engine adapters (as Cloud's
        build_cloud_adapter_registry does) fails closed: a duckdb/sqlite source
        resolves but no adapter claims it, so execute returns a fail-closed
        error rather than falling back to the warehouse SqlAdapter."""
        from unittest.mock import patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.source import (
            DuckDBSourceConfig,
            SQLiteSourceConfig,
        )

        registry = AdapterRegistry(project=local_project(tmp_path))
        # Only the warehouse adapter — no DuckDB/SQLite adapter (the Cloud shape).
        registry.register(
            SqlAdapter(
                project=local_project(tmp_path),
                dbt_project_path=None,
                profile_type="postgres",
            )
        )

        resolved = (
            DuckDBSourceConfig(type="duckdb", path=":memory:")
            if source_type == "duckdb"
            else SQLiteSourceConfig(type="sqlite", path="db.sqlite")
        )
        query = SqlQuery(sql="SELECT 1", source="local_file")
        with patch.object(registry._resolver, "resolve", return_value=resolved):
            result = registry.execute(query)

        assert not result.is_success
        error = str(result.error or "").lower()
        # An adapter IS registered for "sql" (SqlAdapter) — it's the source
        # (duckdb/sqlite) that has no claimant. The message must not claim
        # the type itself is unregistered, and must name the source problem.
        assert "no adapter registered for query type" not in error
        assert "sql" in error
        assert source_type in error

    def test_no_adapter_registered_for_query_type_names_the_type(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """No adapter at all claims the query's type: the message says the
        type itself is unregistered, distinct from the type-registered/
        source-unsupported case above."""
        from dbt_charts.core.compile.models.query.normalized import HttpQuery

        registry = AdapterRegistry(project=local_project(tmp_path))
        registry.register(
            SqlAdapter(
                project=local_project(tmp_path),
                dbt_project_path=None,
                profile_type="postgres",
            )
        )

        result = registry.execute(HttpQuery(url="https://example.com/"))

        assert not result.is_success
        error = str(result.error or "").lower()
        assert "http" in error
        assert "no adapter registered" in error

    def test_duckdb_routes_to_duckdb_adapter_not_sql_adapter(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """DuckDB is owned exclusively by DuckDBAdapter — a registry with both
        registered routes a duckdb source there, never to SqlAdapter's pool."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        registry = AdapterRegistry(project=local_project(tmp_path))
        duckdb_adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        registry.register(duckdb_adapter)
        sql_adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="postgres",
        )
        registry.register(sql_adapter)

        query = SqlQuery(sql="SELECT 1", source="local_file")
        resolved = DuckDBSourceConfig(type="duckdb", path=":memory:")
        assert registry.get_adapter(query, resolved) is duckdb_adapter

    def test_sql_adapter_never_claims_duckdb_source(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """SqlAdapter._can_execute rejects duckdb sources unconditionally — the
        allow_duckdb escape hatch is gone; DuckDBAdapter owns duckdb exclusively."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.source import DuckDBSourceConfig

        sql_adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="postgres",
        )
        query = SqlQuery(sql="SELECT 1", source="local_file")
        resolved = DuckDBSourceConfig(type="duckdb", path=":memory:")
        assert sql_adapter._can_execute(query, resolved) is False


class TestClose:
    def test_close_calls_sql_adapter_close(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = AdapterRegistry(project=local_project(tmp_path))
        adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="postgres",
        )
        registry.register(adapter)

        closed = False
        original_close = adapter.close

        def track_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        adapter.close = track_close  # type: ignore[method-assign]
        registry.close()
        assert closed

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
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        project = local_project(tmp_path)
        materializer = FileSourceMaterializer(project, TrivialDuckDBCache())
        closed = False
        original_close = materializer.close

        def track_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        materializer.close = track_close  # type: ignore[method-assign]
        registry = AdapterRegistry(project=project, file_materializer=materializer)

        registry.close()

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
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "orders.csv").write_text("region,amount\nNorth,1\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
        )
        project = local_project(tmp_path)
        built: list[FileSourceMaterializer] = []

        def factory() -> FileSourceMaterializer:
            materializer = FileSourceMaterializer(project, TrivialDuckDBCache())
            built.append(materializer)
            return materializer

        registry = AdapterRegistry(
            project=project,
            project_sources=load_project_sources(project),
            file_materializer_factory=factory,
        )
        registry.execute(SqlQuery(sql="SELECT * FROM orders", source="marts"))
        assert len(built) == 1
        closed = False
        original_close = built[0].close

        def track_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        built[0].close = track_close  # type: ignore[method-assign]

        registry.close()

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
    ) -> AdapterRegistry:
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        return build_adapter_registry(
            local_project(tmp_path),
            read_only=False,
            resolver=AllowlistedSourceResolver(),
        )

    def test_sourceless_values_query_renders_on_hosted_surface(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import ValuesQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = registry.execute(ValuesQuery(rows=[{"metric": "revenue", "n": 42}]))
        assert result.error is None, result.error
        assert result.data == [{"metric": "revenue", "n": 42}]

    def test_sourceless_sql_query_still_rejected_on_hosted_surface(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        registry = self._hosted_registry(tmp_path, local_project)
        result = registry.execute(SqlQuery(sql="SELECT 1 AS one", source=None))
        assert result.error is not None
        assert "source name required" in str(result.error).lower()

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
        result = registry.execute(SchemaQuery(source="unregistered"))
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
        result = registry.execute(
            SchemaQuery(source="unregistered"), query_name="my_named_query"
        )
        assert result.error is not None
        assert "my_named_query" in str(result.error)


class TestSourcelessSqlRejectedOnDefaultResolver:
    """execute() itself closes the default-DuckDB fallback — not just
    resolve_source_config(None). Before this fix, a sourceless SqlQuery on
    the plain (non-hosted) DefaultSourceResolver silently ran against the
    build-time :memory: DuckDBAdapter instead of raising."""

    def _registry_with_a_configured_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> AdapterRegistry:
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  warehouse:\n    type: duckdb\n    path: ':memory:'\n"
        )
        return build_adapter_registry(local_project(tmp_path), read_only=False)

    def test_sourceless_sql_query_raises_no_default_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        registry = self._registry_with_a_configured_source(tmp_path, local_project)
        result = registry.execute(SqlQuery(sql="SELECT 1 AS one", source=None))
        assert result.error is not None
        assert "Name a source for the query" in str(result.error)

    def test_sourceless_values_query_still_succeeds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import ValuesQuery

        registry = self._registry_with_a_configured_source(tmp_path, local_project)
        result = registry.execute(ValuesQuery(rows=[{"metric": "revenue", "n": 42}]))
        assert result.error is None, result.error
        assert result.data == [{"metric": "revenue", "n": 42}]


def test_file_source_without_a_materializer_still_refuses(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A file source resolves fine — but a registry with no file_materializer
    (the default) still cannot run one. The seam is opt-in: a caller that wants
    ad-hoc file-source queries must pass file_materializer/file_materializer_factory
    to AdapterRegistry (or ProjectSession), never get one invented for free.
    """
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    registry = AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=load_project_sources(local_project(tmp_path)),
    )

    result = registry.execute(SqlQuery(sql="SELECT 1", source="marts"))

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
    from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    project = local_project(tmp_path)
    registry = AdapterRegistry(
        project=project,
        project_sources=load_project_sources(project),
        file_materializer=FileSourceMaterializer(project, TrivialDuckDBCache()),
    )

    result = registry.execute(SchemaQuery(source="marts"))

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
    this path via AdapterRegistry.execute).
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

    def _registry_with_materializer(
        self, project: FilesystemProject
    ) -> AdapterRegistry:
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        return AdapterRegistry(
            project=project,
            project_sources=load_project_sources(project),
            file_materializer=FileSourceMaterializer(project, TrivialDuckDBCache()),
        )

    def test_sql_query_returns_rows_from_a_real_csv(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        project = self._csv_project(
            tmp_path, local_project, [("North", 100), ("South", 200)]
        )
        registry = self._registry_with_materializer(project)

        result = registry.execute(
            SqlQuery(sql="SELECT * FROM orders ORDER BY amount", source="marts")
        )

        assert result.error is None, result.error
        assert [r["region"] for r in result.data] == ["North", "South"]

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

        result = registry.execute(SqlQuery(sql="SELECT * FROM orders", source="marts"))

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

        result = registry.execute(SqlQuery(sql="SELECT * FROM orders", source="marts"))

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

        result = registry.execute(
            SqlQuery(sql="SELECT nonexistent_col FROM orders", source="marts")
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

        strict_result = registry.execute(SqlQuery(sql=sql, source="marts"))
        assert strict_result.error is not None

        lenient_result = registry.execute(
            SqlQuery(sql=sql, source="marts", lenient_variables=True)
        )
        assert lenient_result.error is None, lenient_result.error

    def test_query_ref_is_expanded_before_dispatch(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Regression test for the ordering hazard: the guard used to sit
        above _compose_query_refs, so a file-source query carrying
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

        result = registry.execute(
            board.queries["wrapper"], board=board, query_name="wrapper"
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
        limited = registry.execute(
            SqlQuery(sql="SELECT * FROM orders", source="marts", limit=2)
        )
        assert limited.error is None, limited.error
        assert len(limited.data) == 2
        assert limited.truncated_reason is None

        # No author limit, ceiling below the source's row count: truncates at
        # the ceiling and flags it — the same contract SqlAdapter honors.
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        unbounded = registry.execute(
            SqlQuery(sql="SELECT * FROM orders", source="marts")
        )
        assert unbounded.error is None, unbounded.error
        assert len(unbounded.data) == 3
        assert unbounded.truncated_reason == "max_rows"
