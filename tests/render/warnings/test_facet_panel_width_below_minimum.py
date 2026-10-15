"""Tests for the FACET_PANEL_WIDTH_BELOW_MINIMUM render-warning detector.

Detection rule: fires when a faceted chart's inner unit spec width — the same
per-panel width facet_panel_width() computes at resolve and render — is below
FACET_MIN_PANEL_PX.

These are unit tests for the comparison logic only, driven by a hand-built
vega_specs entry — they do NOT prove the real render pipeline produces that
shape for a real board. The end-to-end gate for that is
dbt-charts/tests/core/render/test_facet_panel_width_below_minimum_warning_e2e.py,
which goes through compile() -> render() and reads RenderResult.warnings.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.diagnostics import WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    facet_panel_width_below_minimum as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _rows(n_groups: int) -> list[dict[str, Any]]:
    return [{"grp": f"g{i:02d}", "cat": "a", "val": i} for i in range(n_groups)]


def _faceted_bar(columns: str = "grp") -> BarChart:
    return BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        multiples=MultiplesConfig(columns=columns),
    )


def _ctx(
    chart: Any, rows: list[dict[str, Any]], panel_width: float | None
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    vega_specs: dict[str, dict[str, Any]] = {}
    if panel_width is not None:
        vega_specs[resolved.id] = {
            "facet": {"column": {"field": "grp"}},
            "spec": {"width": panel_width},
        }
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs=vega_specs,
    )


def test_fires_when_panel_width_below_the_floor() -> None:
    """6 column panels crammed into a narrow width — each panel undercuts
    the legibility floor."""
    chart = _faceted_bar()
    rows = _rows(6)
    ctx = _ctx(chart, rows, panel_width=80.0)
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM.code
    assert w.chart == "c1"
    assert "6" in w.message
    assert "80" in w.message
    assert w.fix is not None


def test_silent_when_panel_width_at_or_above_the_floor() -> None:
    chart = _faceted_bar()
    rows = _rows(2)
    assert detector.detect(_ctx(chart, rows, panel_width=240.0)) == []


def test_silent_when_chart_has_no_multiples() -> None:
    chart = BarChart(id="c1", type="bar", query_name="q", x="cat", y="val")
    rows = _rows(6)
    assert detector.detect(_ctx(chart, rows, panel_width=80.0)) == []


def test_silent_on_non_cartesian_chart() -> None:
    """A chart family with no `multiples` field (e.g. line without it set)
    never reaches the facet-shape check."""
    chart = LineChart(id="c1", type="line", query_name="q", x="cat", y="val")
    rows = _rows(6)
    assert detector.detect(_ctx(chart, rows, panel_width=80.0)) == []


def test_silent_when_chart_missing_from_vega_specs() -> None:
    """No spec generated for this chart (e.g. spec build failed) — nothing
    to judge."""
    chart = _faceted_bar()
    rows = _rows(6)
    assert detector.detect(_ctx(chart, rows, panel_width=None)) == []


def test_silent_when_spec_is_not_faceted() -> None:
    """multiples is authored but the emitted spec has no `facet` key —
    defensive: the detector reads the spec's own shape, not the chart's."""
    chart = _faceted_bar()
    resolved = make_test_resolved_chart(chart, _rows(6))
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _rows(6)},
        vega_specs={resolved.id: {"mark": "bar", "encoding": {}}},
    )
    assert detector.detect(ctx) == []
