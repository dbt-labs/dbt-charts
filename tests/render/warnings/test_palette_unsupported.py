"""Tests for the PALETTE_UNSUPPORTED render-warning detector.

Detection rule: fires when a chart's resolved requested_alias_palette is set —
the chart authored a WARN-PALETTE-UNSUPPORTED anti-pattern name (e.g.
"RdYlGn"). palette() resolves the substitute silently at compile time; this
detector is the render-stage surface for the nudge.
"""

from __future__ import annotations

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


def test_fires_when_chart_authors_alias_palette() -> None:
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
    # Message must name the substitute palette an author can act on, not
    # unlabeled hex stops (docs/guides/palette-resolver.md promises this).
    assert "dbt-div-crimson-green" in w.message
    assert "#" not in w.message
    assert w.fix is not None


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
