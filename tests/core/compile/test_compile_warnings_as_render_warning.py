"""CompileResult.warnings carries Diagnostic objects with stable codes.

Migration test: every compile-time warning emitter (orphan-chart detector,
validate_compiled_queries) must produce Diagnostic entries — never bare
strings. Each emitter owns a stable SCREAMING_SNAKE_CASE code housed in a
module under the ``dbt_charts.core.render.warnings`` package.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.compiler import validate_compiled_queries
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.inspect.query_validator import QueryDiagnostic

_ORPHAN_YAML = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  used:
    query: q
    type: bar
    x: x
    y: y
  unused:
    query: q
    type: bar
    x: x
    y: y
rows:
  - used
"""


class TestOrphanChartEmitsDiagnostic:
    def test_orphan_chart_warning_is_render_warning(self) -> None:
        result = compile(_ORPHAN_YAML)

        assert result.success
        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert isinstance(w, Diagnostic)
        assert w.code == "WARN-UNREFERENCED-CHART"
        assert "unused" in w.message

    def test_every_compile_warning_validates_as_render_warning(self) -> None:
        """Parametric guard: any warning on CompileResult must be a Diagnostic."""
        result = compile(_ORPHAN_YAML)
        for w in result.warnings:
            # Round-trip validates the wire shape — extra=forbid catches drift.
            Diagnostic.model_validate(w.model_dump())


class TestValidateCompiledQueriesEmitsDiagnostic:
    """validate_compiled_queries must produce Diagnostic objects, not strings."""

    def test_reaggregation_diagnostic_becomes_render_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = compile(
            """
source: examples_db
queries:
  q: SELECT 1 AS x
charts:
  c:
    query: q
    type: bar
    x: x
    y: x
rows:
  - c
"""
        )
        assert result.success

        def fake_validate_query(
            *_a: object, **_kw: object
        ) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
            return (
                [
                    QueryDiagnostic(
                        code="WARN-REAGGREGATION",
                        severity="warning",
                        message="Join may cause row multiplication",
                        recommendation="Verify join keys",
                    )
                ],
                [],
            )

        monkeypatch.setattr(
            "dbt_charts.core.inspect.query_validator.validate_query",
            fake_validate_query,
        )
        validate_compiled_queries(result)

        assert result.warnings, (
            "WARN-REAGGREGATION diagnostic must surface as a warning"
        )
        # All entries must be Diagnostic.
        for w in result.warnings:
            assert isinstance(w, Diagnostic)
        codes = [w.code for w in result.warnings]
        assert "WARN-REAGGREGATION" in codes

    def test_suppressed_warnings_are_render_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = compile(
            """
source: examples_db
queries:
  q:
    sql: SELECT 1 AS x
    ignore: [WARN-REAGGREGATION]
charts:
  c:
    query: q
    type: bar
    x: x
    y: x
rows:
  - c
"""
        )
        assert result.success

        def fake_validate_query(
            *_a: object,
            suppress: set[str] | None = None,
            return_suppressed: bool = False,
            **_kw: object,
        ) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
            suppressed = QueryDiagnostic(
                code="WARN-REAGGREGATION",
                severity="warning",
                message="reaggregation suppressed",
            )
            if suppress and "WARN-REAGGREGATION" in suppress:
                return ([], [suppressed])
            return ([suppressed], [])

        monkeypatch.setattr(
            "dbt_charts.core.inspect.query_validator.validate_query",
            fake_validate_query,
        )
        validate_compiled_queries(result)
        assert result.suppressed_warnings, (
            "expected WARN-REAGGREGATION to be suppressed"
        )
        assert all(isinstance(w, Diagnostic) for w in result.suppressed_warnings)
        assert "WARN-REAGGREGATION" in {w.code for w in result.suppressed_warnings}
