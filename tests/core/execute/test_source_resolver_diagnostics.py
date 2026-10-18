"""Regression tests: source-resolver errors must survive `to_diagnostic()`.

The resolver's raise sites feed `available=` into a code whose
`hint_generator` splits it as a comma-separated string. Passing a raw `list`
crashed inside `build_diagnostic` — after the error was already raised, so the
`except DbtChartsError` handlers that call `to_diagnostic()` (board_to_dict,
the LSP server) took an AttributeError instead of reporting the real problem.
The same raw list also leaked Python list repr into the user-facing message.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_NOT_FOUND
from dbt_charts.core.execute.source_resolver import (
    AllowlistedSourceResolver,
    DefaultSourceResolver,
)


def _project_sources(**sources: dict[str, Any]) -> ProjectSourcesConfig:
    ps = ProjectSourcesConfig()
    ps.sources = dict(sources)
    return ps


class TestSourceResolverDiagnostics:
    def test_unknown_source_diagnostic_renders(self):
        """ERR-SOURCE-NOT-FOUND from the shared lookup converts cleanly."""
        resolver = DefaultSourceResolver()
        ps = _project_sources(
            postgres={"type": "postgres"}, duckdb={"type": "duckdb", "path": ":memory:"}
        )

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="postgre",
                board_sources={},
                project_sources=ps,
                dbt_context=None,
                query_name="q",
            )

        diag = exc_info.value.to_diagnostic()
        assert "duckdb, postgres" in diag.message
        assert "['duckdb'" not in diag.message
        assert diag.hint is not None
        assert "postgres" in diag.hint

    def test_unknown_source_diagnostic_renders_allowlisted(self):
        """The allowlisted resolver raises the same code from its own site."""
        resolver = AllowlistedSourceResolver()
        ps = _project_sources(postgres={"type": "postgres"})

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="postgre",
                board_sources={},
                project_sources=ps,
                dbt_context=None,
                query_name="q",
            )

        diag = exc_info.value.to_diagnostic()
        assert "['postgres']" not in diag.message
        assert diag.hint is not None
        assert "postgres" in diag.hint

    def test_available_as_list_renders_without_list_repr(self):
        """A raise site writing `available=sorted(...)` — the natural
        expression, not a manual join — must render cleanly through
        `to_diagnostic()`. This is the exact bug: a raw list into a code
        whose hint_generator expected a comma-joined string crashed inside
        `build_diagnostic`, after the error had already raised."""
        exc = DbtChartsError.from_code(
            ERR_SOURCE_NOT_FOUND,
            query_name="q",
            source="postgre",
            available=["postgres", "duckdb"],
            advice="",
        )

        diag = exc.to_diagnostic()
        assert "postgres, duckdb" in diag.message
        assert "['postgres'" not in diag.message
        assert diag.hint is not None
        assert "postgres" in diag.hint

    def test_invalid_source_type_diagnostic_renders(self):
        """ERR-SOURCE-INVALID-TYPE lists valid types without list repr."""
        resolver = DefaultSourceResolver()
        ps = _project_sources(warehouse={"type": "postgre"})

        with pytest.raises(DbtChartsError) as exc_info:
            resolver.resolve(
                authored="warehouse",
                board_sources={},
                project_sources=ps,
                dbt_context=None,
                query_name="q",
            )

        diag = exc_info.value.to_diagnostic()
        assert "postgres" in diag.message
        assert "['" not in diag.message
