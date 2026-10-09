"""Unit tests for AllowlistedSourceResolver.

Pins the closed-allowlist resolver's three rejection rules (inline dict,
cross-file `#`, unknown name with sorted allowed list), board-level inline
rejection, the disabled dbt-context fallback, that the structured-error
payload does not echo connection-parameter values, and delegation of accepted
inputs to DefaultSourceResolver's shared lookup.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_SOURCE_INLINE_FORBIDDEN,
    ERR_SOURCE_NOT_FOUND,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_SOURCE_CROSS_FILE_FORBIDDEN,
    ERR_SOURCE_NOT_NAMED,
)


def _project_sources(**sources: dict[str, Any]) -> ProjectSourcesConfig:
    ps = ProjectSourcesConfig()
    ps.sources = dict(sources)
    return ps


class TestAllowlistedSourceResolver:
    """Contract tests for AllowlistedSourceResolver."""

    def test_rejects_inline_per_query_source(self):
        """FR-002: per-query `source: {type: postgres, host: ...}` is refused."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        authored = {
            "type": "postgres",
            "host": "evil.example.com",
            "dbname": "x",
            "user": "u",
            "password": "p",
        }
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored=authored,
                board_sources={},
                project_sources=_project_sources(
                    examples_db={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_INLINE_FORBIDDEN

    def test_rejects_inline_board_level_source(self):
        """FR-002: board-level `board.sources` entries with connection params are refused."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        board_sources = {
            "db": {
                "type": "postgres",
                "host": "evil.example.com",
                "dbname": "x",
                "user": "u",
                "password": "p",
            }
        }
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="db",
                board_sources=board_sources,
                project_sources=_project_sources(
                    examples_db={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_INLINE_FORBIDDEN

    def test_inline_rejection_does_not_leak_secret_values(self):
        """Inline-rejection error must not echo connection-parameter values.

        Inline source rejections fire on attacker-supplied payloads and on
        accidental commits of real credentials alike. The structured-error
        payload surfaces via `QueryResult.error` and logs; echoing
        `password`, `host`, etc. compounds the leak.
        """
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        secret = "do-not-leak-this-prod-password"
        host = "internal.prod.example"
        authored = {
            "type": "postgres",
            "host": host,
            "dbname": "analytics",
            "user": "ro",
            "password": secret,
        }
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored=authored,
                board_sources={},
                project_sources=_project_sources(
                    examples_db={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=None,
            )
        rendered = str(exc_info.value)
        payload = exc_info.value.fields["offending_value"]
        assert secret not in rendered
        assert secret not in payload
        assert host not in rendered
        assert host not in payload
        assert "postgres" in payload

    def test_rejects_sourceless_query(self):
        """A sourceless query (authored=None) is refused on the closed allowlist.

        Locally, DefaultSourceResolver returns None here so an ad-hoc query runs
        against the scratch DuckDB; on a hosted/multi-tenant surface that fallback
        is an SSRF / local-file-read vector, so every query must name a source.
        This is the trust-boundary rule, not a required model field.
        """
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored=None,
                board_sources={},
                project_sources=_project_sources(
                    warehouse={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_NOT_NAMED
        assert "names no source" in str(exc_info.value).lower()

    def test_rejects_hash_anchor_reference(self):
        """FR-003: any string containing `#` is refused (cross-file reference)."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="_sources.yaml#evil",
                board_sources={},
                project_sources=_project_sources(
                    examples_db={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_CROSS_FILE_FORBIDDEN
        assert exc_info.value.fields["offending_value"] == "_sources.yaml#evil"

    def test_rejects_unknown_name(self):
        """FR-001/FR-005: unknown name refused; allowed list is sorted ascending."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        project_sources = _project_sources(
            examples_db={"type": "duckdb", "path": "examples.duckdb"},
            dundersign_db={"type": "duckdb", "path": "dundersign.duckdb"},
        )
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="my_warehouse",
                board_sources={},
                project_sources=project_sources,
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_NOT_FOUND
        assert exc_info.value.fields["available"] == ["dundersign_db", "examples_db"]

    def test_unknown_name_lists_allowed(self):
        """FR-005: rendered message includes the sorted allowed list."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        project_sources = _project_sources(
            zeta={"type": "duckdb", "path": "z.duckdb"},
            alpha={"type": "duckdb", "path": "a.duckdb"},
            mid={"type": "duckdb", "path": "m.duckdb"},
        )
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="missing",
                board_sources={},
                project_sources=project_sources,
                dbt_context=None,
            )
        message = str(exc_info.value)
        alpha_idx = message.find("alpha")
        mid_idx = message.find("mid")
        zeta_idx = message.find("zeta")
        assert 0 < alpha_idx < mid_idx < zeta_idx

    def test_disabled_dbt_fallback(self):
        """D4: unknown name raises even when dbt_context is set."""
        from dbt_charts.core.execute.source_resolver import (
            AllowlistedSourceResolver,
            DbtContext,
        )

        resolver = AllowlistedSourceResolver()
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="unknown_name",
                board_sources={},
                project_sources=_project_sources(
                    examples_db={"type": "duckdb", "path": ":memory:"}
                ),
                dbt_context=DbtContext(),
            )
        assert exc_info.value.code == ERR_SOURCE_NOT_FOUND

    def test_accepts_allowlisted_name_via_shared_helper(self):
        """Accepted inputs delegate to DefaultSourceResolver's shared lookup."""
        from dbt_charts.core.execute.source_resolver import (
            AllowlistedSourceResolver,
            DefaultSourceResolver,
        )

        project_sources = _project_sources(
            examples_db={"type": "duckdb", "path": "examples.duckdb"}
        )
        resolver = AllowlistedSourceResolver()
        with patch.object(
            DefaultSourceResolver, "resolve", autospec=True
        ) as mock_super:
            mock_super.return_value = DuckDBSourceConfig(
                type="duckdb", path="examples.duckdb"
            )
            result = resolver.resolve(
                authored="examples_db",
                board_sources={},
                project_sources=project_sources,
                dbt_context=None,
            )
        assert mock_super.call_count == 1
        _, kwargs = mock_super.call_args
        assert kwargs["authored"] == "examples_db"
        assert kwargs["project_sources"] is project_sources
        assert kwargs["dbt_context"] is None
        assert isinstance(result, DuckDBSourceConfig)

    def test_accepts_allowlisted_name_returns_typed_config(self):
        """End-to-end: accepted name resolves through super() to typed SourceConfig."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        project_sources = _project_sources(
            examples_db={"type": "duckdb", "path": "examples.duckdb"}
        )
        result = resolver.resolve(
            authored="examples_db",
            board_sources={},
            project_sources=project_sources,
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)
        assert result.path == "examples.duckdb"

    def test_empty_allowlist_with_authored_source(self):
        """Spec Edge Case 5: empty allowlist + authored source raises NOT-FOUND with allowed=[]."""
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="anything",
                board_sources={},
                project_sources=_project_sources(),
                dbt_context=None,
            )
        assert exc_info.value.code == ERR_SOURCE_NOT_FOUND
        assert exc_info.value.fields["available"] == []
        assert "none configured" in str(exc_info.value)

    def test_compiler_expanded_allowlisted_name_in_board_sources_passes_through(self):
        """Allowlisted name appearing in board_sources (compiler-expanded) resolves to project def.

        The compiler populates board.sources with all project sources (resolved to
        inline dicts). AllowlistedSourceResolver must not refuse a source name that
        is in both board_sources and project_sources.sources — the project allowlist
        takes precedence and super().resolve uses the project definition.
        """
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        project_sources = _project_sources(
            examples_db={"type": "duckdb", "path": "examples.duckdb"}
        )
        # Simulate compiler putting the project source into board.sources
        board_sources = {"examples_db": {"type": "duckdb", "path": "examples.duckdb"}}
        result = resolver.resolve(
            authored="examples_db",
            board_sources=board_sources,
            project_sources=project_sources,
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig)
        assert result.path == "examples.duckdb"

    def test_malicious_override_of_allowlisted_name_uses_project_definition(self):
        """Security regression: board overriding an allowlisted name uses project definition.

        A board author can write `sources: {examples_db: {type: postgres, host: evil}}`
        to attempt to hijack an allowlisted name. The resolver skips the inline-forbidden
        raise (because `examples_db` IS in the allowlist) but delegates to
        DefaultSourceResolver.resolve with board_sources={} — so super() looks up
        `examples_db` from project_sources, not from the malicious board override.
        The resolved config MUST be the safe project definition, not the attacker dict.
        """
        from dbt_charts.core.execute.source_resolver import AllowlistedSourceResolver

        resolver = AllowlistedSourceResolver()
        project_sources = _project_sources(
            examples_db={"type": "duckdb", "path": "examples.duckdb"}
        )
        # Malicious board override: attacker puts inline postgres under the allowlisted name
        malicious_board_sources = {
            "examples_db": {
                "type": "postgres",
                "host": "evil.example.com",
                "dbname": "stolen",
                "user": "u",
                "password": "secret",
            }
        }
        # Must resolve to the project's safe duckdb definition, NOT the inline postgres
        result = resolver.resolve(
            authored="examples_db",
            board_sources=malicious_board_sources,
            project_sources=project_sources,
            dbt_context=None,
        )
        assert isinstance(result, DuckDBSourceConfig), (
            f"Malicious board override must not win over project allowlist: got {type(result)}"
        )
        assert result.path == "examples.duckdb"
