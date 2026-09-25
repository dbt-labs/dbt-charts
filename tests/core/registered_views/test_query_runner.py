"""Tests for registry pre-template query execution.

Purpose: Validate path-param materialization, result namespace shape,
         validation error surfacing, slow-query warning emission, and
         reuse of the normal query system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.cache_backend import (
    CachedQueryFailure,
    CacheHit,
    CacheOutcome,
)
from dbt_charts.core.registered_views.models import RegisteredView
from dbt_charts.core.registered_views.query_runner import (
    RegistryQueryError,
    ViewQueryResult,
    materialize_path_params,
    run_registry_queries,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view(queries: dict[str, Any] | None = None) -> RegisteredView:
    return RegisteredView(
        name="entity_table",
        route="/entity/<source>/<schema>/<table>/",
        template="entity/table-index.yaml",
        queries=queries,
    )


def _mock_registry(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a mock AdapterRegistry that returns ``rows`` from execute()."""
    registry = MagicMock()
    registry.execute.return_value = QueryResult(data=rows)
    return registry


def _fake_table_profile_envelope(
    source: str, schema: str, table: str, cols: dict[str, Any]
) -> dict[str, Any]:
    return {
        "sources": {
            source: {"schemas": {schema: {"tables": {table: {"columns": cols}}}}}
        }
    }


# ---------------------------------------------------------------------------
# materialize_path_params
# ---------------------------------------------------------------------------


class TestMaterializePathParams:
    def test_replaces_path_template_string(self) -> None:
        spec = {"type": "schema", "source": "{{ path.source }}", "schema": "analytics"}
        path_params = {"source": "snowflake"}
        result = materialize_path_params(spec, path_params)
        assert result["source"] == "snowflake"
        assert result["schema"] == "analytics"

    def test_replaces_all_path_placeholders(self) -> None:
        spec = {
            "type": "schema",
            "source": "{{ path.source }}",
            "schema": "{{ path.schema }}",
            "table": "{{ path.table }}",
        }
        path_params = {"source": "s", "schema": "sc", "table": "t"}
        result = materialize_path_params(spec, path_params)
        assert result == {"type": "schema", "source": "s", "schema": "sc", "table": "t"}

    def test_ignores_non_path_jinja(self) -> None:
        """Non-path Jinja (e.g. {{ var }}) is left untouched — not our domain."""
        spec = {"sql": "SELECT {{ limit }}", "source": "dw"}
        path_params = {"source": "snowflake"}
        result = materialize_path_params(spec, path_params)
        # sql field is untouched
        assert result["sql"] == "SELECT {{ limit }}"

    def test_handles_whitespace_in_template(self) -> None:
        spec = {"source": "{{   path.source   }}"}
        result = materialize_path_params(spec, {"source": "bigquery"})
        assert result["source"] == "bigquery"

    def test_missing_path_param_raises(self) -> None:
        """Referencing {{ path.X }} when X is not in path_params raises ValueError."""
        spec = {"source": "{{ path.source }}"}
        with pytest.raises(ValueError, match="path.source"):
            materialize_path_params(spec, {})

    def test_does_not_mutate_original_spec(self) -> None:
        spec = {"source": "{{ path.source }}", "type": "schema"}
        path_params = {"source": "dw"}
        result = materialize_path_params(spec, path_params)
        assert spec["source"] == "{{ path.source }}"  # original unchanged
        assert result["source"] == "dw"

    def test_nested_values_not_touched(self) -> None:
        """Only top-level string values are processed; nested dicts/lists are skipped."""
        spec = {"source": "{{ path.source }}", "filters": {"col": "{{ path.col }}"}}
        result = materialize_path_params(spec, {"source": "dw"})
        assert result["source"] == "dw"
        # nested filters not substituted
        assert result["filters"] == {"col": "{{ path.col }}"}


# ---------------------------------------------------------------------------
# ViewQueryResult
# ---------------------------------------------------------------------------


class TestViewQueryResult:
    def test_rows_returns_all_rows(self) -> None:
        rows = [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]
        r = ViewQueryResult(rows=rows)
        assert r.rows == rows

    def test_columns_returns_column_names_in_first_row_order(self) -> None:
        rows = [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]
        r = ViewQueryResult(rows=rows)
        assert r.columns == ["id", "name"]

    def test_columns_empty_when_no_rows(self) -> None:
        r = ViewQueryResult(rows=[])
        assert r.columns == []

    def test_one_returns_single_row(self) -> None:
        rows = [{"id": 1}]
        r = ViewQueryResult(rows=rows)
        assert r.one == {"id": 1}

    def test_one_raises_when_zero_rows(self) -> None:
        r = ViewQueryResult(rows=[])
        with pytest.raises(ValueError, match="one row"):
            _ = r.one

    def test_one_raises_when_multiple_rows(self) -> None:
        r = ViewQueryResult(rows=[{"id": 1}, {"id": 2}])
        with pytest.raises(ValueError, match="one row"):
            _ = r.one


# ---------------------------------------------------------------------------
# run_registry_queries — happy paths (mock adapter_registry)
# ---------------------------------------------------------------------------


class TestRunRegistryQueriesHappyPath:
    def test_no_queries_returns_empty_dict(self) -> None:
        view = _make_view(queries=None)
        result = run_registry_queries(view, {}, MagicMock())
        assert result == {}

    def test_empty_queries_dict_returns_empty_dict(self) -> None:
        view = _make_view(queries={})
        result = run_registry_queries(view, {}, MagicMock())
        assert result == {}

    def test_returns_registry_query_result_for_each_name(self) -> None:
        rows = [{"name": "id", "type": "BIGINT"}, {"name": "status", "type": "VARCHAR"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "dw",
                    "schema": "analytics",
                    "table": "orders",
                }
            }
        )
        registry = _mock_registry(rows)
        result = run_registry_queries(view, {}, registry)

        assert "columns" in result
        assert isinstance(result["columns"], ViewQueryResult)
        assert result["columns"].rows == rows

    def test_path_params_materialized_before_execution(self) -> None:
        """{{ path.X }} is replaced with literal values before normalize_query."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "{{ path.source }}",
                    "schema": "{{ path.schema }}",
                    "table": "{{ path.table }}",
                }
            }
        )
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}
        registry = _mock_registry(rows)
        result = run_registry_queries(view, path_params, registry)

        assert "columns" in result
        assert result["columns"].rows == rows
        # Verify the normalized query that was executed had literals, not templates
        executed_query = registry.execute.call_args[0][0]
        assert executed_query.source == "snowflake"
        assert executed_query.schema_name == "raw"
        assert executed_query.table == "events"

    def test_multiple_queries_all_executed(self) -> None:
        view = _make_view(
            queries={
                "cols": {"type": "schema", "source": "dw", "schema": "s", "table": "t"},
                "profile": {
                    "type": "schema",
                    "source": "dw",
                    "schema": "s",
                    "table": "t",
                    "column": "id",
                },
            }
        )
        registry = MagicMock()
        registry.execute.side_effect = [
            QueryResult(data=[{"name": "id"}]),
            QueryResult(data=[{"name": "id", "type": "BIGINT"}]),
        ]
        result = run_registry_queries(view, {}, registry)

        assert "cols" in result
        assert "profile" in result
        assert registry.execute.call_count == 2
        assert result["cols"].rows[0]["name"] == "id"
        assert result["profile"].rows[0]["name"] == "id"

    def test_result_columns_from_row_keys(self) -> None:
        rows = [{"name": "id", "type": "BIGINT"}, {"name": "status", "type": "VARCHAR"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "dw",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        registry = _mock_registry(rows)
        result = run_registry_queries(view, {}, registry)
        assert set(result["columns"].columns) == {"name", "type"}


# ---------------------------------------------------------------------------
# run_registry_queries — validation errors
# ---------------------------------------------------------------------------


class TestRunRegistryQueriesValidationErrors:
    def test_invalid_query_type_raises_registry_query_error(self) -> None:
        """A query with an unknown type raises RegistryQueryError with view+query context."""
        view = _make_view(
            queries={"broken": {"type": "not_a_real_type", "source": "dw"}}
        )
        with pytest.raises(RegistryQueryError) as exc_info:
            run_registry_queries(view, {}, MagicMock())
        msg = str(exc_info.value)
        assert "entity_table" in msg
        assert "broken" in msg

    def test_missing_path_param_raises_registry_query_error(self) -> None:
        """A query referencing {{ path.X }} when X is missing raises RegistryQueryError."""
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "{{ path.source }}",
                    "schema": "{{ path.schema }}",
                    "table": "{{ path.table }}",
                }
            }
        )
        # path_params is missing "source"
        with pytest.raises(RegistryQueryError) as exc_info:
            run_registry_queries(view, {"schema": "s", "table": "t"}, MagicMock())
        assert "path.source" in str(exc_info.value)

    def test_execution_error_raises_registry_query_error(self) -> None:
        """When the adapter returns an error, RegistryQueryError is raised."""
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "nonexistent",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        registry = MagicMock()
        registry.execute.return_value = QueryResult(
            data=[], error=QueryError("Source not found")
        )

        with pytest.raises(RegistryQueryError) as exc_info:
            run_registry_queries(view, {}, registry)
        assert "Source not found" in str(exc_info.value)

    def test_truncated_result_raises_registry_query_error(self) -> None:
        """A ceiling-truncated result would feed the board-generation template
        incomplete data — RegistryQueryError must raise instead of silently
        proceeding with a partial result."""
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "wh",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        registry = MagicMock()
        registry.execute.return_value = QueryResult(
            data=[{"a": 1}], truncated_reason="max_rows"
        )

        with pytest.raises(RegistryQueryError, match="truncated"):
            run_registry_queries(view, {}, registry)

    def test_schema_query_jinja_in_field_raises_before_execution(self) -> None:
        """{{ path.X }} must be materialized before SchemaQuery validation rejects Jinja."""
        # Passing a {{ }} template without a matching path_param means
        # materialization fails before the SchemaQuery validator ever runs.
        view = _make_view(
            queries={
                "cols": {
                    "type": "schema",
                    "source": "{{ path.missing }}",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        with pytest.raises(RegistryQueryError, match="path.missing"):
            run_registry_queries(view, {}, MagicMock())


# ---------------------------------------------------------------------------
# run_registry_queries — slow-query warning
# ---------------------------------------------------------------------------


class TestSlowQueryWarning:
    def test_slow_query_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A registry query exceeding the threshold emits a logger.warning."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "dw",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        registry = _mock_registry(rows)

        with caplog.at_level(
            logging.WARNING, logger="dbt_charts.core.registered_views.query_runner"
        ):
            run_registry_queries(
                view,
                {},
                registry,
                slow_query_threshold_s=0.0,  # 0 s — always triggers
            )

        slow_records = [r for r in caplog.records if "slow" in r.message.lower()]
        assert slow_records, "Expected a slow-query warning"
        assert "columns" in slow_records[0].message
        assert "entity_table" in slow_records[0].message

    def test_fast_query_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A registry query within threshold emits no slow-query warning."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "dw",
                    "schema": "s",
                    "table": "t",
                }
            }
        )
        registry = _mock_registry(rows)

        with caplog.at_level(
            logging.WARNING, logger="dbt_charts.core.registered_views.query_runner"
        ):
            run_registry_queries(
                view,
                {},
                registry,
                slow_query_threshold_s=9999.0,  # never triggers
            )

        slow_records = [r for r in caplog.records if "slow" in r.message.lower()]
        assert not slow_records


# ---------------------------------------------------------------------------
# Schema query (type: schema) integration via SchemaAdapter — validates D-14
# ---------------------------------------------------------------------------


class TestSchemaQueryAsRegistryQuery:
    """type: schema works as a registry query via the real SchemaAdapter."""

    def test_schema_query_returns_column_rows(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """type: schema is normalized correctly and dispatched to SchemaAdapter."""
        cols = {"order_id": {"type": "BIGINT"}, "status": {"type": "VARCHAR"}}
        envelope = _fake_table_profile_envelope(
            "warehouse", "analytics", "orders", cols
        )

        with patch(
            "dbt_charts.core.inspect.cache_factory.build_resolver",
            return_value=MagicMock(profile_table=MagicMock(return_value=envelope)),
        ):
            from dbt_charts.core.execute.adapters import build_adapter_registry
            from dbt_charts.core.execute.adapters.schema_adapter import SchemaAdapter

            registry = build_adapter_registry(
                local_project(tmp_path), allowed_types={"schema"}
            )
            # Call SchemaAdapter._execute directly to bypass source-resolution;
            # source resolution raises for unknown source names without a dbt project.
            # The SchemaAdapter does not use source_config — it calls build_resolver.
            schema_adapter = next(
                a for a in registry.adapters if isinstance(a, SchemaAdapter)
            )
            from dbt_charts.core.compile.models.query.normalized import SchemaQuery

            query = SchemaQuery(source="warehouse", schema="analytics", table="orders")
            qr = schema_adapter._execute(query)

        assert qr.error is None
        names = {r["name"] for r in qr.data}
        assert names == {"order_id", "status"}

    def test_schema_query_with_path_params_materialized(self) -> None:
        """{{ path.X }} is materialized before the SchemaQuery validator runs.

        SchemaQuery._no_jinja rejects raw Jinja templates, so materialization
        MUST happen before normalize_query is called.
        """
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "{{ path.source }}",
                    "schema": "{{ path.schema }}",
                    "table": "{{ path.table }}",
                }
            }
        )
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}
        registry = _mock_registry(rows)

        result = run_registry_queries(view, path_params, registry)

        assert result["columns"].rows == rows
        # The query passed to execute must have literal source, not Jinja template
        executed_query = registry.execute.call_args[0][0]
        assert executed_query.source == "snowflake"

    def test_schema_query_type_is_schema_not_schema_resolver(self) -> None:
        """The renamed type: schema (not schema_resolver) is accepted."""
        rows = [{"name": "col1", "type": "TEXT"}]
        view = _make_view(
            queries={
                "meta": {
                    "type": "schema",
                    "source": "src",
                    "schema": "sc",
                    "table": "t",
                }
            }
        )
        registry = _mock_registry(rows)
        result = run_registry_queries(view, {}, registry)

        assert "meta" in result
        assert result["meta"].rows == rows


# ---------------------------------------------------------------------------
# run_registry_queries — QueryResultCache
# ---------------------------------------------------------------------------


# This fake's error retry window. Registry queries never cache errors, so
# the value only has to be non-zero for the union arm to be exercised.
_FAILURE_RETRY_WINDOW = timedelta(seconds=900)


class _FakeCache:
    """Minimal in-memory QueryResultCache for testing.

    Supports clock injection to test TTL expiry without real DuckDB.
    """

    def __init__(self) -> None:
        # {key: (rows, rows_at, error, error_at)} — two slots on one entry,
        # exactly like the real backends. Either may be empty.
        self._store: dict[
            tuple[str, str, str],
            tuple[
                list[dict[str, Any]] | None,
                datetime | None,
                Exception | None,
                datetime | None,
            ],
        ] = {}
        self.get_call_count = 0
        self.put_call_count = 0
        # Callable[[], datetime] — injectable for TTL tests
        # Aware UTC, matching the backend contract: a real backend never
        # hands CacheHit/CachedQueryFailure a naive timestamp, so a fake
        # that did would be testing a shape production cannot produce.
        self._now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def get(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        *,
        ttl: timedelta | None = None,
    ) -> CacheOutcome | None:
        self.get_call_count += 1
        entry = self._store.get((source_hash, query_hash, variables_hash))
        if entry is None:
            return None
        rows, rows_at, error, error_at = entry
        now = self._now()
        # Rows first, then the error slot — the real read order.
        if rows_at is not None and (ttl is None or now - rows_at <= ttl):
            return CacheHit(rows=rows or [], written_at=rows_at)
        if error is not None and error_at is not None:
            if now - error_at <= _FAILURE_RETRY_WINDOW:
                return CachedQueryFailure(
                    error_class=type(error).__name__,
                    error_message=str(error),
                    traceback="",
                    failed_at=error_at,
                )
        return None

    def put(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        outcome: list[dict[str, Any]] | Exception,
        *,
        board_slug: str,
        query_name: str,
        **_: Any,
    ) -> None:
        self.put_call_count += 1
        key = (source_hash, query_hash, variables_hash)
        rows, rows_at, error, error_at = self._store.get(key, (None, None, None, None))
        now = self._now()
        if isinstance(outcome, Exception):
            error, error_at = outcome, now
        else:
            # A success clears the error slot in the same write.
            rows, rows_at, error, error_at = outcome, now, None, None
        self._store[key] = (rows, rows_at, error, error_at)

    # Remaining QueryResultCache methods — unused here, signatures match the
    # protocol so _FakeCache type-checks as a QueryResultCache.
    def clear(self, source_hash: str, query_hash: str, variables_hash: str) -> None:
        pass

    def close(self) -> None:
        pass


def _schema_view() -> RegisteredView:
    """A view with a single schema-type pre-template query."""
    return _make_view(
        queries={
            "columns": {
                "type": "schema",
                "source": "{{ path.source }}",
                "schema": "{{ path.schema }}",
                "table": "{{ path.table }}",
            }
        }
    )


class TestRunRegistryQueriesCache:
    def test_second_call_served_from_cache(self) -> None:
        """Two identical calls execute the adapter once; second hit from cache."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _schema_view()
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}

        registry = _mock_registry(rows)
        cache = _FakeCache()

        run_registry_queries(view, path_params, registry, cache=cache)
        run_registry_queries(view, path_params, registry, cache=cache)

        assert registry.execute.call_count == 1, (
            "adapter called twice; expected cache hit"
        )

    def test_different_path_param_executes_again(self) -> None:
        """Different path params produce a different content hash — no cross-path dedup."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _schema_view()

        registry = _mock_registry(rows)
        cache = _FakeCache()

        run_registry_queries(
            view,
            {"source": "snowflake", "schema": "raw", "table": "events"},
            registry,
            cache=cache,
        )
        run_registry_queries(
            view,
            {"source": "snowflake", "schema": "raw", "table": "orders"},
            registry,
            cache=cache,
        )

        assert registry.execute.call_count == 2, (
            "Different path params must produce different cache keys"
        )

    def test_ttl_expiry_forces_re_execution(self) -> None:
        """After the query's resolved ttl expires the adapter runs again."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "{{ path.source }}",
                    "schema": "{{ path.schema }}",
                    "table": "{{ path.table }}",
                    "cache": {"ttl": "1m"},
                }
            }
        )
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}

        registry = _mock_registry(rows)
        cache = _FakeCache()

        # First call — populate cache; clock is at t=0 (datetime.now())
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        cache._now = lambda: t0
        run_registry_queries(view, path_params, registry, cache=cache)

        # Advance clock past the resolved 1m ttl.
        t_expired = t0 + timedelta(seconds=61)
        cache._now = lambda: t_expired
        run_registry_queries(view, path_params, registry, cache=cache)

        assert registry.execute.call_count == 2, (
            "Expected re-execution after TTL expiry"
        )

    def test_no_cache_runs_adapter_every_time(self) -> None:
        """When cache=None the adapter executes on every call (existing behavior)."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _schema_view()
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}

        registry = _mock_registry(rows)

        run_registry_queries(view, path_params, registry, cache=None)
        run_registry_queries(view, path_params, registry, cache=None)

        assert registry.execute.call_count == 2

    def test_cache_stores_result_under_correct_key(self) -> None:
        """Cache.put is called once with the result rows."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _schema_view()
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}

        registry = _mock_registry(rows)
        cache = _FakeCache()

        run_registry_queries(view, path_params, registry, cache=cache)

        assert cache.put_call_count == 1

    def test_cache_false_never_reads_or_writes(self) -> None:
        """A registry query with cache: false always re-executes
        and never writes."""
        rows = [{"name": "id", "type": "BIGINT"}]
        view = _make_view(
            queries={
                "columns": {
                    "type": "schema",
                    "source": "{{ path.source }}",
                    "schema": "{{ path.schema }}",
                    "table": "{{ path.table }}",
                    "cache": False,
                }
            }
        )
        path_params = {"source": "snowflake", "schema": "raw", "table": "events"}

        registry = _mock_registry(rows)
        cache = _FakeCache()

        run_registry_queries(view, path_params, registry, cache=cache)
        run_registry_queries(view, path_params, registry, cache=cache)

        assert registry.execute.call_count == 2, "cache: false must re-run every call"
        assert cache.put_call_count == 0, "cache: false must write nothing"
