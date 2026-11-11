"""`validate`'s render-time suppression reads the engine's host facts, not a Python board."""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api.validate import ValidateResult
from dbt_charts.core.board import partition_compile_warnings
from dbt_charts.core.diagnostics import (
    WARN_REAGGREGATION,
    WARN_REDUNDANT_ENCODING,
    WarningCode,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.project import Project

A = WARN_REAGGREGATION
B = WARN_REDUNDANT_ENCODING


def _warning(code: WarningCode, chart: str | None) -> Diagnostic:
    return Diagnostic.from_code(code, message="m", chart=chart)


def test_partition_takes_a_chart_id_to_codes_map() -> None:
    warnings = [
        _warning(A, "bars"),
        _warning(A, "pie"),
        _warning(A, None),
        _warning(B, "pie"),
    ]
    active, suppressed = partition_compile_warnings(
        warnings, {"bars": {A.code}}, None, frozenset({B.code})
    )
    assert [(w.code, w.chart) for w in suppressed] == [
        (A.code, "bars"),
        (B.code, "pie"),
    ]
    assert [(w.code, w.chart) for w in active] == [(A.code, "pie"), (A.code, None)]


_BOARD = """\
queries:
  revenue:
    sql: |
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
%s
rows:
  - revenue_trend
"""
_CODE = "WARN-MISSING-JOIN-PREDICATE"


def test_validate_suppresses_a_chart_code_the_engine_reports(
    tmp_path: Path,
    local_project: Callable[..., Project],
    validate_text: Callable[..., ValidateResult],
) -> None:
    project = local_project(tmp_path)
    loud = validate_text(_BOARD % "", project=project)
    assert _CODE in [w.code for w in loud.warnings]

    quiet = validate_text(
        _BOARD % f"    warnings_ignore:\n      - {_CODE}", project=project
    )
    assert _CODE not in [w.code for w in quiet.warnings]
