"""Tests for the LEGEND_POSITION_WIDTH_FALLBACK render-warning detector.

Detection rule: fires on a cartesian chart or pie whose resolved
``legend.position_overridden_by_width`` is set -- the tiny width tier
(< 352.5px) forced a cartesian legend back to `top`, or a pie's side key fell
below the wheel, over an authored `legend.position.edge`. The fact is baked at
resolve time (``cartesian_series_naming()`` in ``_axes.py``, ``_resolve_pie``);
this detector only reads it off ``ctx.layout_charts``, the placement-matched
chart instances.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    KpiChart,
)
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_LEGEND_POSITION_WIDTH_FALLBACK, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_position_width_fallback as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_TINY_WIDTH = 200.0
_WIDE_WIDTH = 600.0
_ROWS: list[dict[str, Any]] = [
    {"cat": "a", "series": "s1", "val": 1},
    {"cat": "a", "series": "s2", "val": 2},
]


def _bar(**kwargs: Any) -> BarChart:
    return BarChart(
        **{
            "id": "c1",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "val",
            "color": "series",
            **kwargs,
        }
    )


def _ctx(chart: Any, width: float | None) -> WarningContext:
    resolved = make_test_resolved_chart(chart, _ROWS, width=width)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: _ROWS},
        vega_specs={},
        layout_charts={resolved.id: resolved},
    )


def test_fires_when_tiny_width_overrides_authored_position() -> None:
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"position": {"edge": "bottom"}}}
        )
    )
    warnings = detector.detect(_ctx(chart, _TINY_WIDTH))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_LEGEND_POSITION_WIDTH_FALLBACK.code
    assert w.chart == "c1"
    assert "bottom" in w.message
    assert w.fix is not None


def test_silent_at_a_width_that_fits_a_side_legend() -> None:
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"position": {"edge": "bottom"}}}
        )
    )
    assert detector.detect(_ctx(chart, _WIDE_WIDTH)) == []


def test_silent_when_author_placed_no_position() -> None:
    """Tiny width still forces top, but there was no authored position to
    override -- nothing for the author to be told about."""
    chart = _bar()
    assert detector.detect(_ctx(chart, _TINY_WIDTH)) == []


def test_silent_when_authored_position_already_top() -> None:
    """Authored `position: top` agrees with the tiny-width fallback -- no
    override happened."""
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"position": {"edge": "top"}}}
        )
    )
    assert detector.detect(_ctx(chart, _TINY_WIDTH)) == []


def test_silent_on_non_cartesian_chart() -> None:
    chart = KpiChart(id="c1", type="kpi", query_name="q", value="val")
    assert detector.detect(_ctx(chart, _TINY_WIDTH)) == []


def test_silent_when_chart_absent_from_layout_charts() -> None:
    """Charts not reached by the active layout tree are absent from
    layout_charts -- the detector must skip them."""
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"position": {"edge": "bottom"}}}
        )
    )
    resolved = make_test_resolved_chart(chart, _ROWS, width=_TINY_WIDTH)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _ROWS},
        vega_specs={},
        layout_charts={},  # chart absent from active layout
    )
    assert detector.detect(ctx) == []
