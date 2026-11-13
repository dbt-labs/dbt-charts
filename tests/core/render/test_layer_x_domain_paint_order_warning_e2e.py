"""End-to-end: a layer whose own query contributes x categories the base never
returns, on an axis with no derivable order, pins a paint-order domain and
fires an advisory warning.

Proves the full seam — compile -> render() -> RenderResult.warnings — not a
hand-built WarningContext. The record happens inside the emitter, which for a
Vega-family chart runs during the render-first *sizing* pass and is served from
the render cache in the main pass, so this is the only test that can catch the
sink being opened in the wrong pass.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile
from dbt_charts.core.render import render

from ..._run import Run
from ..._scripted_env import ScriptedBackend, rows_by_sql, scripted_env

_CODE = "WARN-LAYER-X-DOMAIN-PAINT-ORDER"

_BASE_ROWS = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Mar", "revenue": 120.0},
    {"month": "May", "revenue": 140.0},
]
# Month abbreviations are not dates and not numbers — nothing can order them,
# so the layer-only categories can only be appended in paint order.
_DISJOINT_LAYER_ROWS = [
    {"month": "Feb", "target": 90.0},
    {"month": "Apr", "target": 110.0},
    {"month": "Jun", "target": 130.0},
]
# Same categories as the base: the union is the base's own domain, nothing is
# appended, and there is no ambiguity to report.
_COVERED_LAYER_ROWS = [
    {"month": "Jan", "target": 90.0},
    {"month": "Mar", "target": 110.0},
    {"month": "May", "target": 130.0},
]

_BOARD = """
title: Layered months
charts:
  revenue:
    query: base
    type: bar
    x: month
    y: revenue
    layers:
      - type: line
        query: overlay
        y: target
queries:
  base:
    sql: SELECT * FROM base_t
    source: test_source
  overlay:
    sql: SELECT * FROM overlay_t
    source: test_source
rows:
  - revenue
"""


def _make_executor(board: object, query_registry: dict, layer_rows: list[dict]):
    rows_by_query = {"base": _BASE_ROWS, "overlay": layer_rows}
    backend = ScriptedBackend(
        side_effect=rows_by_sql(
            {query_registry[name].sql: rows for name, rows in rows_by_query.items()}
        )
    )
    return Run(board, env=scripted_env(backend))


def _render(layer_rows: list[dict]):
    result = compile(_BOARD)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, layer_rows)
    return render(result.board, executor.ctx, executor.ledger, format="svg")


def test_layer_only_categories_on_an_unorderable_axis_warn() -> None:
    render_result = _render(_DISJOINT_LAYER_ROWS)

    codes = {w.code for w in render_result.warnings}
    assert _CODE in codes, (
        "the overlay query contributes Feb/Apr/Jun, which the base never "
        "returns and nothing can order — the axis is paint-ordered and the "
        "warning must reach RenderResult.warnings"
    )
    warning = next(w for w in render_result.warnings if w.code == _CODE)
    assert warning.chart == "revenue"
    assert "month" in warning.message
    assert warning.fix


def test_a_layer_that_adds_no_categories_is_silent() -> None:
    render_result = _render(_COVERED_LAYER_ROWS)

    codes = {w.code for w in render_result.warnings}
    assert _CODE not in codes
