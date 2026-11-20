"""Tests for the PALETTE_UNSUPPORTED render-warning detector.

Detection rule: fires when a chart's resolved warned_palette is set —
the chart authored a WARN-PALETTE-UNSUPPORTED anti-pattern name (e.g.
"RdYlGn"). The palette draws as authored; this detector is the render-stage
surface for the recommendation.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_PALETTE_UNSUPPORTED, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    palette_unsupported as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _make_ctx(chart: BarChart) -> WarningContext:
    resolved = make_test_resolved_chart(chart)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: []},
    )


def test_fires_when_chart_authors_a_warned_palette() -> None:
    """A chart authoring a known anti-pattern alias must fire."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="x",
        y="val",
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "RdYlGn"}}}
        ),
    )
    warnings = detector.detect(_make_ctx(chart))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_PALETTE_UNSUPPORTED.code
    assert w.chart == "c1"
    assert "RdYlGn" in w.message
    # The recommendation is a palette name, not hex stops.
    assert "consider dbt-div-crimson-green instead" in w.message
    assert "#" not in w.message
    assert w.fix is not None
    assert "dbt-div-crimson-green" in w.fix


def test_parula_recommends_dbt_seq_blue() -> None:
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="x",
        y="val",
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "parula"}}}
        ),
    )
    (w,) = detector.detect(_make_ctx(chart))
    assert "dbt-seq-blue" in w.message


def test_no_fire_on_non_alias_palette() -> None:
    """A chart with a hand-authored (non-alias) palette must not fire."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="x",
        y="val",
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": ["#aabbcc", "#112233"]}}}
        ),
    )
    assert detector.detect(_make_ctx(chart)) == []


def test_no_fire_when_no_palette_authored() -> None:
    """A chart with no style override at all must not fire."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="x", y="val")
    assert detector.detect(_make_ctx(chart)) == []


def _gradient_warnings(chart_yaml: str) -> list[Diagnostic]:
    from ...core._svg_render import render_board_svg_result  # noqa: PLC0415

    board = f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{x: a, y: b, v: 1}}
      - {{x: c, y: b, v: 5}}
charts:
  c:
{chart_yaml}
rows: [c]
"""
    result = render_board_svg_result(board)
    assert not result.chart_errors
    return [w for w in result.warnings if w.code == WARN_PALETTE_UNSUPPORTED.code]


@pytest.mark.parametrize(
    ("palette", "recommended"),
    [("RdYlGn", "dbt-div-crimson-green"), ("parula_r", "dbt-seq-blue")],
)
def test_fires_on_a_heatmap_gradient(palette: str, recommended: str) -> None:
    (w,) = _gradient_warnings(
        "    type: heatmap\n    query: q\n    x: x\n    y: y\n    color: v\n"
        "    style:\n      color:\n        gradient:\n"
        f"          palette: {palette}\n          hinge: 3\n"
    )
    assert f"consider {recommended} instead" in w.message
    assert w.path == "charts.c"


def test_fires_on_a_table_column_scale() -> None:
    (w,) = _gradient_warnings(
        "    type: table\n    query: q\n    style:\n      columns:\n        v:\n"
        "          scale:\n            background:\n              palette: parula\n"
    )
    assert w.path == "charts.c.style.columns.v.scale.background"
    assert "dbt-seq-blue" in w.message
