"""Unit tests for DefaultSourceResolver.

Pins the resolver contract: board-level lookup, project-source lookup,
inline-dict acceptance, default-source application (FR-008), dbt-context
fallback, parse_source_config wire-up, and the FR-007 guarantee that the
resolver never touches build_adapter.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    PostgresSourceConfig,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND


def _project_sources(**sources: dict[str, Any]) -> ProjectSourcesConfig:
    ps = ProjectSourcesConfig()
    ps.sources = dict(sources)
    return ps


class TestDefaultSourceResolver:
    """Contract tests for DefaultSourceResolver."""

    def test_board_level_lookup(self):
        """String source name found in board_sources is resolved to typed SourceConfig."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        board_sources = {"my_db": {"type": "duckdb", "path": ":memory:"}}
        result = resolver.resolve(
            authored="my_db",
            board_sources=board_sources,
            project_sources=_project_sources(),
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)
        assert result.path == ":memory:"

    def test_project_source_lookup(self):
        """String source name found only in project_sources is resolved."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        project_sources = _project_sources(
            analytics={"type": "duckdb", "path": "analytics.duckdb"}
        )
        result = resolver.resolve(
            authored="analytics",
            board_sources={},
            project_sources=project_sources,
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)
        assert result.path == "analytics.duckdb"

    def test_board_level_takes_precedence_over_project(self):
        """Board-level source overrides project-level source of same name."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        board_sources = {"db": {"type": "duckdb", "path": "board_level.duckdb"}}
        project_sources = _project_sources(
            db={"type": "duckdb", "path": "project_level.duckdb"}
        )
        result = resolver.resolve(
            authored="db",
            board_sources=board_sources,
            project_sources=project_sources,
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)
        assert result.path == "board_level.duckdb"

    def test_inline_dict_accepted(self):
        """Authored inline dict is parsed and returned as typed SourceConfig."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        result = resolver.resolve(
            authored={"type": "duckdb", "path": ":memory:"},
            board_sources={},
            project_sources=_project_sources(),
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)

    def test_authored_none_no_default_returns_none(self):
        """authored=None with no project default returns None."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        result = resolver.resolve(
            authored=None,
            board_sources={},
            project_sources=_project_sources(),
            dbt_context=None,
        )
        assert result is None

    def test_dbt_context_allows_unknown_string_through(self):
        """With no `sources:` configured at all, an unknown string source and
        dbt_context returns None (not raises) — the pure-dbt-jinja workflow, no
        dbt_charts.yml `sources:` block to check the name against."""
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        resolver = DefaultSourceResolver()
        result = resolver.resolve(
            authored="some_dbt_profile",
            board_sources={},
            project_sources=_project_sources(),
            dbt_context=DbtContext(),
        )
        assert result is None

    def test_dbt_context_does_not_mask_unknown_name_when_sources_are_configured(self):
        """Regression (dbt-labs/dbt-charts#45): once `sources:` is configured,
        an unrecognized name must raise ERR-SOURCE-NOT-FOUND even when a dbt
        project is in scope — it must never silently fall through to the
        engine's default in-memory DuckDB connection. Without this, a typo'd
        or generic source name ("default", "duckdb") executed successfully
        against `:memory:` instead of failing."""
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.source_resolver import (
            DbtContext,
            DefaultSourceResolver,
        )

        resolver = DefaultSourceResolver()
        project_sources = _project_sources(
            analytics_warehouse={"type": "duckdb", "path": "warehouse.duckdb"}
        )
        for offending in ("analytics_warehous", "default", "duckdb"):
            with pytest.raises(DbtChartsError) as exc_info:
                resolver.resolve(
                    authored=offending,
                    board_sources={},
                    project_sources=project_sources,
                    dbt_context=DbtContext(),
                )
            assert exc_info.value.code == ERR_SOURCE_NOT_FOUND

    def test_unknown_string_source_no_dbt_context_raises(self):
        """Unknown string source with no dbt_context raises ERR-SOURCE-NOT-FOUND."""
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        project_sources = _project_sources(known={"type": "duckdb", "path": ":memory:"})
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="unknown_db",
                board_sources={},
                project_sources=project_sources,
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_NOT_FOUND

    def test_unknown_source_message_carries_caller_supplied_query_name(self):
        """ERR-SOURCE-NOT-FOUND's message_template requires `query_name` since
        the merge kept compile's query-context wording. A caller that knows
        the query name (executor.py, agent_api.query) must see it in the
        rendered message, not the ad-hoc default.
        """
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        project_sources = _project_sources(known={"type": "duckdb", "path": ":memory:"})
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="unknown_db",
                board_sources={},
                project_sources=project_sources,
                dbt_context=None,
                query_name="revenue_by_month",
            )
        assert "revenue_by_month" in str(exc_info.value)

    def test_result_is_typed_source_config(self):
        """Resolver always returns a typed SourceConfig instance (never a raw dict)."""
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        resolver = DefaultSourceResolver()
        board_sources = {
            "pg": {
                "type": "postgres",
                "host": "db.example.com",
                "dbname": "analytics",
                "user": "ro",
                "password": "secret",
            }
        }
        result = resolver.resolve(
            authored="pg",
            board_sources=board_sources,
            project_sources=_project_sources(),
            dbt_context=None,
        )
        assert isinstance(result, PostgresSourceConfig)

    @pytest.mark.parametrize(
        ("authored", "board_sources", "project_sources_fn", "dbt_context"),
        [
            # inline dict
            (
                {"type": "duckdb", "path": ":memory:"},
                {},
                lambda: _project_sources(),
                None,
            ),
            # board-level name
            (
                "my_db",
                {"my_db": {"type": "duckdb", "path": ":memory:"}},
                lambda: _project_sources(),
                None,
            ),
            # project-level name
            (
                "proj_db",
                {},
                lambda: _project_sources(
                    proj_db={"type": "duckdb", "path": ":memory:"}
                ),
                None,
            ),
            # authored=None (no project default)
            (None, {}, lambda: _project_sources(), None),
        ],
    )
    def test_default_resolver_does_not_invoke_build_adapter(
        self, monkeypatch, authored, board_sources, project_sources_fn, dbt_context
    ):
        """FR-007: resolver never calls build_adapter for any input shape."""
        import dbt_charts.core.execute.adapters.dbt_adapter_factory as factory
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        called = []
        monkeypatch.setattr(factory, "build_adapter", lambda *a, **kw: called.append(1))

        resolver = DefaultSourceResolver()
        resolver.resolve(
            authored=authored,
            board_sources=board_sources,
            project_sources=project_sources_fn(),
            dbt_context=dbt_context,
        )
        assert not called, (
            "build_adapter was invoked inside the resolver — FR-007 violation"
        )  # noqa: E501
